"""learn.py 单元测试: 学堂读取协议(假 transport 全离线, 零真实账号).

路由构造器供 controller/plugin/cli 学堂测试复用(campus 先例).
"""

import base64
import json
from urllib.parse import parse_qsl

import pytest

from shuimu.core.learn import (
    FILE_CATEGORIES_PATH,
    FILES_PATH,
    HOMEWORK_DETAIL_PATH,
    HOMEWORK_PATHS,
    INDEX_PAGE_PATH,
    LEARN_BASE,
    NOTICE_DETAIL_PATH,
    NOTICE_PATHS,
    ProtocolChangedError,
    SEMESTER_PATH,
    SessionExpiredError,
    LearnClient,
    LearnCourse,
    ao_data,
    decode_notice_html,
    download_course_file_url,
    extract_csrf,
    extract_query_ids,
    html_to_text,
    json_rows,
    learn_url,
    parse_learn_date,
)
from shuimu.core.campus_net import CookieJar, MemoryTransport

WLKCID = "2025-2026-1-00000001"
CSRF = "tok-a1b2"
SEMESTER = "2025-2026-1"

CSRF_PAGE = (f'<script src="/f/wlxt/common/languagejs?lang=zh"></script>'
             f'<a href="/b/kc/xxx?_csrf={CSRF}">link</a>')
SEMESTER_JSON = {"result": {"id": SEMESTER, "xnxq": SEMESTER,
                            "kssj": "2025-09-15", "jssj": "2026-01-16"}}


def _post_url(path):
    """POST 端点含 _csrf 的完整 URL."""
    return f"{learn_url(path)}?_csrf={CSRF}"


def add_csrf_route(transport):
    """公共前奏: 学堂首页(带 _csrf 令牌)."""
    transport.add("GET", learn_url(INDEX_PAGE_PATH), text=CSRF_PAGE)


def add_semester_route(transport):
    """学期端点."""
    transport.add("GET", f"{learn_url(SEMESTER_PATH)}?_csrf={CSRF}",
                  text=json.dumps(SEMESTER_JSON))


def add_courses_route(transport, rows):
    """课程列表端点(resultList 包裹)."""
    path = (f"/b/wlxt/kc/v_wlkc_xs_xkb_kcb_extend/student"
            f"/loadCourseBySemesterId/{SEMESTER}/zh")
    transport.add("GET", f"{learn_url(path)}?_csrf={CSRF}",
                  text=json.dumps({"result": "success", "resultList": rows}))


def add_homework_routes(transport, pending=(), submitted=(), graded=()):
    """三态作业端点(object.aaData 包裹; URL 不含课程, 按注册顺序消费)."""
    for state, rows in (("pending", pending), ("submitted", submitted),
                        ("graded", graded)):
        transport.add("POST", _post_url(HOMEWORK_PATHS[state]),
                      text=json.dumps({"result": "success",
                                       "object": {"aaData": list(rows)}}))


def add_notice_routes(transport, active=(), expired=()):
    """公告两端点(未过期 + 已过期; URL 不含课程, 按注册顺序消费)."""
    for scope, rows in (("active", active), ("expired", expired)):
        transport.add("POST", _post_url(NOTICE_PATHS[scope]),
                      text=json.dumps({"result": "success",
                                       "object": {"aaData": list(rows)}}))


def add_files_route(transport, wlkcid, rows, categories=()):
    """课件列表(object 直接为数组)与分类端点(object.rows)."""
    transport.add(
        "GET",
        f"{learn_url(FILES_PATH)}?wlkcid={wlkcid}&size=200&_csrf={CSRF}",
        text=json.dumps({"result": "success", "object": list(rows)}))
    transport.add(
        "GET",
        f"{learn_url(FILE_CATEGORIES_PATH)}?wlkcid={wlkcid}&_csrf={CSRF}",
        text=json.dumps(
            {"object": {"rows": [{"kjflid": cid, "bt": name}
                                 for cid, name in categories]}}))


def add_download_route(transport, path, payload: bytes):
    """下载端点(原始字节响应)."""
    transport.add("GET", f"{learn_url(path)}{_and_csrf()}",
                  headers=[("Content-Type", "application/octet-stream")],
                  content=payload)


def _and_csrf():
    """下载 URL 的 _csrf 追加段(download path 自带 query)."""
    return f"&_csrf={CSRF}"


def make_client(transport):
    """带共享 jar 的客户端(jar 预置 webvpn 会话 cookie)."""
    jar = CookieJar()
    jar.load({"webvpn.tsinghua.edu.cn": {"wengine_vpn_ticket": "t1"}})
    return LearnClient(transport, jar)


class TestLearnUrl:
    """URL 组装."""

    def test_prefix_and_csrf_appended_by_client(self):
        """路径 -> WebVPN 直连 URL; _csrf 按 query 有无选 ? 或 &."""
        assert learn_url("/b/x") == LEARN_BASE + "/b/x"


class TestParsers:
    """防御式解析工具."""

    def test_extract_csrf_found(self):
        """首页令牌可抓."""
        assert extract_csrf(CSRF_PAGE) == CSRF

    def test_extract_csrf_missing_raises(self):
        """无令牌抛 ProtocolChangedError."""
        with pytest.raises(ProtocolChangedError):
            extract_csrf("<html>empty</html>")

    def test_ao_data_json_array_in_form(self):
        """aoData 是 JSON 数组串, 经 urlencode 传输(非 multipart)."""
        body = "aoData=" + ao_data([("wlkcid", WLKCID)])
        pairs = dict(parse_qsl(body))
        assert json.loads(pairs["aoData"]) == [
            {"name": "wlkcid", "value": WLKCID}]

    def test_decode_notice_html_roundtrip(self):
        """Base64 正文可解码; 损坏输入原样返回."""
        html = "<p>第一周公告</p>"
        encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
        assert decode_notice_html(encoded) == html
        assert decode_notice_html("!!!not-base64!!!") == "!!!not-base64!!!"
        assert decode_notice_html("") == ""

    def test_html_to_text(self):
        """标签清理 + 实体解码 + 空白折叠."""
        assert html_to_text("<p>a &amp; b</p>\n<script>x</script>c") == \
            "a & b c"

    def test_extract_query_ids(self):
        """按出现序取 href query 参数值, 去重."""
        markup = ('<a href="/d/f?fileId=A1">1</a>'
                  '<a href="/d/f?fileId=B2">2</a>'
                  '<a href="/d/f?fileId=A1">again</a>'
                  '<a href="/d/f?other=x">no</a>')
        assert extract_query_ids(markup, "fileId") == ["A1", "B2"]
        assert not extract_query_ids(markup, "wjid")

    def test_parse_learn_date(self):
        """时间串取日期段; 无日期返回 None."""
        assert parse_learn_date("2026-11-20 23:59") == "2026-11-20"
        assert parse_learn_date("2026-11-20") == "2026-11-20"
        assert parse_learn_date("") is None

    def test_json_rows_multiple_wrappers(self):
        """resultList / object.aaData / object 数组 / 裸数组都能取."""
        assert json_rows({"resultList": [1]}, "resultList") == [1]
        assert json_rows({"object": {"aaData": [2]}}, "aaData") == [2]
        assert json_rows({"object": [3]}, "aaData") == [3]
        assert json_rows([4], "aaData") == [4]
        assert json_rows({"result": "success"}, "aaData") == []


class TestSessionExpired:
    """会话失效三触发."""

    def _expired(self, transport, **kwargs):
        transport.add("GET", learn_url(INDEX_PAGE_PATH), **kwargs)
        with pytest.raises(SessionExpiredError):
            LearnClient(transport, CookieJar()).csrf()

    def test_403(self):
        """403 -> 失效."""
        self._expired(MemoryTransport(), status=403)

    def test_redirect_to_login(self):
        """302 落回 WebVPN 登录页 -> 失效."""
        self._expired(MemoryTransport(), status=302,
                      location="https://webvpn.tsinghua.edu.cn/login?x=1")

    def test_login_timeout_in_page(self):
        """200 页面带 login_timeout -> 失效."""
        self._expired(MemoryTransport(), text="some login_timeout page")


class TestLearnClient:
    """端点方法(全套假路由)."""

    @pytest.fixture
    def client(self):
        """预置前奏路由(首页令牌+学期)的客户端."""
        transport = MemoryTransport()
        add_csrf_route(transport)
        add_semester_route(transport)
        return make_client(transport)

    def test_csrf_cached_single_fetch(self, client):
        """令牌抓一次后缓存."""
        transport = client._transport  # pylint: disable=protected-access
        assert client.csrf() == CSRF
        assert client.csrf() == CSRF
        assert len(transport.requests) == 1

    def test_current_semester(self, client):
        """学期 id 与标签."""
        semester = client.current_semester()
        assert semester.semester_id == SEMESTER
        assert semester.label == SEMESTER

    def test_courses(self, client):
        """课程列表字段映射."""
        add_courses_route(client._transport, [  # pylint: disable=protected-access
            {"wlkcid": WLKCID, "kcm": "AI 编程进阶", "kch": "CS999",
             "kxh": "1", "jsm": "张老师"}])
        courses = client.courses(SEMESTER)
        assert courses == [LearnCourse(WLKCID, "AI 编程进阶", "CS999",
                                       "1", "张老师")]

    def test_courses_protocol_change(self, client):
        """缺 resultList 抛 ProtocolChangedError."""
        transport = client._transport  # pylint: disable=protected-access
        path = (f"/b/wlxt/kc/v_wlkc_xs_xkb_kcb_extend/student"
                f"/loadCourseBySemesterId/{SEMESTER}/zh")
        transport.add("GET", f"{learn_url(path)}?_csrf={CSRF}",
                      text=json.dumps({"result": "success"}))
        with pytest.raises(ProtocolChangedError):
            client.courses(SEMESTER)

    def test_homework_states_and_fields(self, client):
        """三态合并, 字段与补交布尔映射."""
        transport = client._transport  # pylint: disable=protected-access
        add_homework_routes(
            transport,
            pending=[{"zyid": "Z1", "xszyid": "S1", "bt": "作业一",
                      "jzsj": "2026-11-20 23:59", "bjjzsj": "2026-11-22",
                      "sfbj": "是", "scsj": None}],
            submitted=[{"zyid": "Z2", "bt": "作业二", "scsj": "2026-11-10"}],
            graded=[{"zyid": "Z3", "bt": "作业三", "cj": "95",
                     "pynr": "不错"}])
        merged = client.homework_all(WLKCID)
        assert [item.state for item in merged] == \
            ["pending", "submitted", "graded"]
        first = merged[0]
        assert (first.zyid, first.bt, first.jzsj, first.sfbj) == \
            ("Z1", "作业一", "2026-11-20 23:59", True)
        assert merged[2].cj == "95" and merged[2].pynr == "不错"

    def test_homework_detail(self, client):
        """作业描述 msg 富文本."""
        transport = client._transport  # pylint: disable=protected-access
        transport.add("POST", _post_url(HOMEWORK_DETAIL_PATH),
                      text=json.dumps({"msg": "<p>提交 PDF</p>"}))
        assert client.homework_detail("Z1") == "<p>提交 PDF</p>"

    def test_homework_attachments(self, client):
        """viewCj 页抓附件 fileId."""
        transport = client._transport  # pylint: disable=protected-access
        url = (f"{learn_url('/f/wlxt/kczy/zy/student/viewCj')}"
               f"?wlkcid={WLKCID}&zyid=Z1&_csrf={CSRF}")
        transport.add("GET", url,
                      text='<div class="list fujian">'
                           '<a href="/b/dl?fileId=F9">a.pdf</a></div>')
        assert client.homework_attachments(WLKCID, "Z1") == ["F9"]

    def test_notices_merged_and_decoded(self, client):
        """未过期+已过期合并, Base64 正文解码."""
        transport = client._transport  # pylint: disable=protected-access
        body = "<p>开学须知</p>"
        encoded = base64.b64encode(body.encode("utf-8")).decode("ascii")
        add_notice_routes(
            transport,
            active=[{"ggid": "G1", "bt": "公告一", "ggnr": encoded,
                     "fbsj": "2026-09-15", "sfyd": "否"}],
            expired=[{"ggid": "G2", "bt": "公告二", "ggnr": "",
                      "sfyd": "是", "fjmc": "a.pdf"}])
        notices = client.notices(WLKCID)
        assert [item.ggid for item in notices] == ["G1", "G2"]
        assert notices[0].body_html == body and not notices[0].sfyd
        assert notices[1].sfyd and notices[1].fjmc == "a.pdf"

    def test_notice_attachments(self, client):
        """公告详情页抓附件 wjid."""
        transport = client._transport  # pylint: disable=protected-access
        url = (f"{learn_url(NOTICE_DETAIL_PATH)}"
               f"?wlkcid={WLKCID}&id=G1&_csrf={CSRF}")
        transport.add("GET", url,
                      text='<a class="ml-10" href="/f/dl?wjid=W7">a.pdf</a>')
        assert client.notice_attachments(WLKCID, "G1") == ["W7"]

    def test_files_and_categories(self, client):
        """课件列表(object 数组)与分类表."""
        transport = client._transport  # pylint: disable=protected-access
        add_files_route(
            transport, WLKCID,
            [{"kjxxid": "K1", "wjid": "W1", "bt": "第一周课件",
              "ms": "讲义", "scsj": "2026-09-15", "wjdx": 2048,
              "kjflid": "C1"},
             {"kjxxid": "K2", "wjid": "W2", "bt": "第二周",
              "wjdx": "4096"}],
            categories=[("C1", "讲义")])
        files = client.files(WLKCID)
        assert files[0].bt == "第一周课件" and files[0].wjdx == 2048
        assert files[1].wjdx == 4096      # 字符串数字可转
        assert client.file_categories(WLKCID) == {"C1": "讲义"}

    def test_download_bytes_binary(self, client):
        """下载返回原始字节(非 utf-8 序列不被破坏)."""
        transport = client._transport  # pylint: disable=protected-access
        payload = b"\x89PNG\x00\xff-bin\x80"
        add_download_route(transport, download_course_file_url("W1"),
                           payload)
        assert client.download_bytes(download_course_file_url("W1")) == \
            payload

    def test_download_rejects_html_page(self, client):
        """HTML 响应不当文件存盘."""
        transport = client._transport  # pylint: disable=protected-access
        path = download_course_file_url("W2")
        transport.add("GET", f"{learn_url(path)}&_csrf={CSRF}",
                      headers=[("Content-Type", "text/html")],
                      text="<html>error</html>")
        with pytest.raises(Exception) as exc_info:
            client.download_bytes(path)
        assert not isinstance(exc_info.value, SessionExpiredError)

    def test_get_json_rejects_non_json(self):
        """非 JSON 响应抛 ProtocolChangedError."""
        transport = MemoryTransport()
        add_csrf_route(transport)
        transport.add("GET", f"{learn_url(SEMESTER_PATH)}?_csrf={CSRF}",
                      text="<html>not json</html>")
        client = make_client(transport)
        with pytest.raises(ProtocolChangedError):
            client.current_semester()
