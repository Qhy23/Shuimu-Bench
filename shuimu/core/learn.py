"""网络学堂(learn.tsinghua.edu.cn)读取协议: 复用校园会话的 WebVPN 直连.

会话前提: core/campus.py 登录建立的 webvpn cookie(wengine_vpn_ticket,
CookieJar 精确 netloc 匹配自动携带) -- 学堂 URL 一律写为 WebVPN 主机
映射前缀形式, 与 INFO 同域, 无需单独漫游:

  https://webvpn.tsinghua.edu.cn/https/{加密主机段}{原路径}

流程: GET 学堂首页抓 _csrf 令牌 -> 学期 -> 课程列表(wlkcid 为主键)
-> 各课作业三态/公告/课件(JSON 端点, POST 列表为 DataTables 风格
aoData 表单) -> 课件/附件下载(响应体即文件字节).

协议行为参考 thu-learn-lib(MIT)逆向结论, 代码自写. 服务端改版风险:
URL/判定串集中在本模块常量区; 页面或 JSON 不可识别抛
ProtocolChangedError, 会话失效抛 SessionExpiredError, 均不崩溃.

零自动联网: 本模块只在被显式调用(用户点刷新/下载)时发请求.
"""

import base64
import html as html_lib
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from shuimu.core.campus_net import (
    CookieJar,
    Transport,
    encode_form,
)

# ---- 协议常量 (服务端改版时单点维护) ----

WEBVPN_HOST = "webvpn.tsinghua.edu.cn"
WEBVPN_HTTPS = f"https://{WEBVPN_HOST}/https/"
# learn.tsinghua.edu.cn 在 WebVPN 的加密主机段(服务端固定映射, 来源
# thu-learn-lib HOST_MAP; 若服务端更换, 用 test/workspace/learn_probe.py
# 重新探测回填)
LEARN_SEGMENT = ("77726476706e69737468656265737421"
                 "fcf2408e297e7c4377068ea48d546d30ca8cc97bcc")
LEARN_BASE = WEBVPN_HTTPS + LEARN_SEGMENT

INDEX_PAGE_PATH = "/f/wlxt/index/course/student/"          # _csrf 抓取页
SEMESTER_PATH = "/b/kc/zhjw_v_code_xnxq/getCurrentAndNextSemester"
COURSES_PATH = ("/b/wlxt/kc/v_wlkc_xs_xkb_kcb_extend/student"
                "/loadCourseBySemesterId/{semester_id}/zh")
HOMEWORK_PATHS = {
    "pending": "/b/wlxt/kczy/zy/student/zyListWj",
    "submitted": "/b/wlxt/kczy/zy/student/zyListYjwg",
    "graded": "/b/wlxt/kczy/zy/student/zyListYpg",
}
HOMEWORK_DETAIL_PATH = "/b/wlxt/kczy/zy/student/detail"
HOMEWORK_VIEW_PATH = "/f/wlxt/kczy/zy/student/viewCj"      # 附件 id 抓取页
HOMEWORK_FILE_PATH = ("/b/wlxt/kczy/zy/student/downloadFile"
                      "/{wlkcid}/{file_id}")
NOTICE_PATHS = {
    "active": "/b/wlxt/kcgg/wlkc_ggb/student/pageListXsbyWgq",
    "expired": "/b/wlxt/kcgg/wlkc_ggb/student/pageListXsbyYgq",
}
NOTICE_DETAIL_PATH = "/f/wlxt/kcgg/wlkc_ggb/student/beforeViewXs"
FILES_PATH = ("/b/wlxt/kj/wlkc_kjxxb/student"
              "/kjxxbByWlkcidAndSizeForStudent")
FILE_CATEGORIES_PATH = "/b/wlxt/kj/wlkc_kjflb/student/pageList"
FILE_DOWNLOAD_PATH = "/b/wlxt/kj/wlkc_kjxxb/student/downloadFile"

LOGIN_TIMEOUT_MARK = "login_timeout"
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
CSRF_RE = re.compile(r"_csrf=([^\"&\s]+)")
YES_MARK = "是"


class LearnError(Exception):
    """学堂读取流程可预期失败(消息可直接展示给用户)."""


class ProtocolChangedError(LearnError):
    """页面/JSON 结构与已知协议不符, 疑似学校服务端改版."""


class SessionExpiredError(LearnError):
    """校园会话已失效(403/被重定向回登录页), 需重新登录."""


# ---- 结果数据 (纯数据) ----

@dataclass
class LearnSemester:
    """当前学期."""

    semester_id: str    # 如 "2025-2026-1"
    label: str = ""


@dataclass
class LearnCourse:
    """一门学堂课程; wlkcid 是后续一切请求的主键."""

    wlkcid: str
    kcm: str = ""       # 课名
    kch: str = ""       # 课号
    kxh: str = ""       # 课序
    jsm: str = ""       # 教师


@dataclass
class LearnHomework:
    """一份作业; state 来自拉取时的三态列表."""

    wlkcid: str
    zyid: str           # 作业 id(详情/附件入口)
    state: str          # pending|submitted|graded
    xszyid: str = ""    # 学生作业 id(提交/查看已交入口)
    bt: str = ""        # 标题
    jzsj: str = ""      # 截止时间
    bjjzsj: str = ""    # 补交截止
    sfbj: bool = False  # 允许补交
    scsj: str = ""      # 提交时间(未交为空)
    cj: str = ""        # 成绩
    pynr: str = ""      # 评语


@dataclass
class LearnNotice:
    """一条课程公告; body_html 为 Base64 解码后的富文本."""

    wlkcid: str
    ggid: str
    bt: str = ""        # 标题
    body_html: str = ""
    fbsj: str = ""      # 发布时间
    jzsj: str = ""      # 截止时间
    sfyd: bool = False  # 已读
    fjmc: str = ""      # 附件名(有则详情页有附件)


@dataclass
class LearnFile:
    """一个课件文件."""

    wlkcid: str
    kjxxid: str         # 课件条目 id
    wjid: str = ""      # 文件 id(下载入口)
    bt: str = ""        # 标题
    ms: str = ""        # 描述
    scsj: str = ""      # 上传时间
    wjdx: int = 0       # 大小(字节)
    kjflid: str = ""    # 分类 id


# ---- 解析工具 (正则/解码, 防御式) ----

def learn_url(path: str) -> str:
    """学堂路径 -> WebVPN 直连完整 URL."""
    return LEARN_BASE + path


def extract_csrf(markup: str) -> str:
    """学堂首页里的 _csrf 令牌; 不可识别抛 ProtocolChangedError."""
    for match in CSRF_RE.finditer(markup or ""):
        token = match.group(1).strip()
        if token:
            return token
    raise ProtocolChangedError("学堂首页未找到 _csrf 令牌")


def ao_data(pairs: list) -> str:
    """DataTables 风格 aoData 表单值: JSON 数组串(urlencoded 传输)."""
    return json.dumps([{"name": key, "value": value}
                       for key, value in pairs], ensure_ascii=False)


def decode_notice_html(raw: str) -> str:
    """公告正文 Base64 -> utf-8 HTML; 损坏输入原样返回."""
    text = (raw or "").strip()
    if not text:
        return ""
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded).decode("utf-8", "replace")
    except ValueError:
        return raw


def html_to_text(markup: str) -> str:
    """HTML -> 单行纯文本投影(预览/待办标题用; 不求完整)."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", markup or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def extract_query_ids(markup: str, param: str) -> list:
    """页面所有链接 href 的 query 参数值(如附件 fileId/wjid), 按出现序."""
    ids = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', markup or ""):
        match = re.search(rf"[?&]{param}=([^&\"'#]+)", href)
        if match is not None and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def parse_learn_date(text: str):
    """学堂时间串(如 '2026-11-20 23:59') -> 'YYYY-MM-DD' 或 None."""
    found = re.search(r"\d{4}-\d{2}-\d{2}", str(text or ""))
    return found.group(0) if found else None


def json_rows(data, *keys) -> list:
    """防御式取行列表: 依次在 data / data['object'] / data['result'] 下
    按 keys 找 list; 容器本身是 list 也接受; 都无则空列表."""
    candidates = [data]
    if isinstance(data, dict):
        candidates.extend(
            data.get(key) for key in ("object", "result"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
        if not isinstance(candidate, dict):
            continue
        for key in keys:
            value = candidate.get(key)
            if isinstance(value, list):
                return value
    return []


def _scalar(row: dict, key: str, default="") -> str:
    """行字段转短字符串(防御式: None/数字/布尔安全落默认或文本)."""
    value = row.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value)


# ---- 会话失效判定 ----

def _is_webvpn_login(url: str) -> bool:
    """落点是否为 WebVPN 登录页(会话失效的标志)."""
    parts = urlsplit(url)
    return parts.netloc == WEBVPN_HOST and parts.path == "/login"


# ---- 协议客户端 ----

class LearnClient:
    """一次学堂读取事务的协议执行器(每事务新建; cookie 由外部 jar 承载,
    复用校园登录会话). 数据端点均为单跳, 无需手动跟重定向."""

    def __init__(self, transport: Transport, jar: CookieJar,
                 on_step=None):
        self._transport = transport
        self._jar = jar
        self._on_step = on_step
        self._csrf = ""

    def _fetch(self, url: str, method: str = "GET",
               body: str = None):
        """单跳请求: 注入 Cookie 头, 回收 Set-Cookie, 逐响应判会话失效."""
        headers = {}
        cookie = self._jar.header_for(url)
        if cookie:
            headers["Cookie"] = cookie
        response = self._transport.fetch(url, method=method, body=body,
                                         headers=headers)
        self._jar.store_from_response(url, response)
        if self._on_step:
            self._on_step(method, url, response)
        self._check_expired(url, response)
        return response

    @staticmethod
    def _check_expired(url: str, response) -> None:
        """403 / 重定向落登录页 / 响应带 login_timeout -> 会话失效."""
        if response.status == 403:
            raise SessionExpiredError("校园会话已失效(403), 请重新登录")
        if response.status in _REDIRECT_STATUSES:
            location = response.header("Location") or ""
            if (LOGIN_TIMEOUT_MARK in location
                    or _is_webvpn_login(urljoin(url, location))):
                raise SessionExpiredError(
                    "校园会话已失效(被重定向回登录页), 请重新登录")
        elif LOGIN_TIMEOUT_MARK in response.text[:4096]:
            raise SessionExpiredError("校园会话已失效, 请重新登录")

    def _url(self, path: str, csrf: str = None) -> str:
        """路径 -> 完整 URL, 追加 _csrf query(令牌缺省自动抓取并缓存)."""
        url = learn_url(path)
        token = csrf if csrf is not None else self._csrf
        if not token:
            token = self.csrf()
        url += ("&" if "?" in url else "?") + f"_csrf={token}"
        return url

    def _get_json(self, path: str, csrf: str = None) -> dict:
        """GET 一个 JSON 端点; 非 dict 响应抛 ProtocolChangedError."""
        response = self._fetch(self._url(path, csrf))
        try:
            data = json.loads(response.text)
        except ValueError as exc:
            raise ProtocolChangedError(
                f"学堂端点响应不是 JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ProtocolChangedError(f"学堂端点响应结构异常: {path}")
        return data

    def _post_json(self, path: str, pairs: list,
                   csrf: str = None) -> dict:
        """POST 一个 aoData 表单 JSON 端点."""
        body = encode_form([("aoData", ao_data(pairs))])
        response = self._fetch(self._url(path, csrf), "POST", body)
        try:
            data = json.loads(response.text)
        except ValueError as exc:
            raise ProtocolChangedError(
                f"学堂端点响应不是 JSON: {path}") from exc
        if not isinstance(data, dict):
            raise ProtocolChangedError(f"学堂端点响应结构异常: {path}")
        return data

    # -- 学期与课程 --

    def csrf(self) -> str:
        """从学堂首页抓 _csrf 令牌(实例内缓存)."""
        if self._csrf:
            return self._csrf
        response = self._fetch(learn_url(INDEX_PAGE_PATH))
        self._csrf = extract_csrf(response.text)
        return self._csrf

    def current_semester(self) -> LearnSemester:
        """当前学期(getCurrentAndNextSemester 取当前项)."""
        data = self._get_json(SEMESTER_PATH + "?_csrf=" + self.csrf())
        result = data.get("result")
        if not isinstance(result, dict) or not result.get("id"):
            raise ProtocolChangedError("学期端点响应缺 result.id")
        semester_id = str(result["id"]).strip()
        return LearnSemester(semester_id,
                             label=str(result.get("xnxq") or semester_id))

    def courses(self, semester_id: str) -> list:
        """某学期课程列表(一次拉全量, 无分页)."""
        path = COURSES_PATH.format(semester_id=semester_id)
        data = self._get_json(path, csrf=self.csrf())
        rows = json_rows(data, "resultList")
        if not rows:
            raise ProtocolChangedError("课程列表端点响应缺 resultList")
        return [LearnCourse(
            wlkcid=_scalar(row, "wlkcid"),
            kcm=_scalar(row, "kcm"),
            kch=_scalar(row, "kch"),
            kxh=_scalar(row, "kxh"),
            jsm=_scalar(row, "jsm"),
        ) for row in rows if isinstance(row, dict) and row.get("wlkcid")]

    # -- 作业 --

    def homework(self, wlkcid: str, state: str,
                 csrf: str = None) -> list:
        """某课单态作业列表; state 为 pending|submitted|graded."""
        data = self._post_json(HOMEWORK_PATHS[state],
                               [("wlkcid", wlkcid)], csrf=csrf)
        rows = json_rows(data, "aaData")
        return [self._parse_homework(row, wlkcid, state)
                for row in rows if isinstance(row, dict) and row.get("zyid")]

    @staticmethod
    def _parse_homework(row: dict, wlkcid: str, state: str) -> LearnHomework:
        return LearnHomework(
            wlkcid=wlkcid,
            zyid=_scalar(row, "zyid"),
            state=state,
            xszyid=_scalar(row, "xszyid"),
            bt=_scalar(row, "bt"),
            jzsj=_scalar(row, "jzsj"),
            bjjzsj=_scalar(row, "bjjzsj"),
            sfbj=_scalar(row, "sfbj") == YES_MARK,
            scsj=_scalar(row, "scsj"),
            cj=_scalar(row, "cj"),
            pynr=_scalar(row, "pynr"),
        )

    def homework_all(self, wlkcid: str, csrf: str = None) -> list:
        """某课三态作业合并列表."""
        merged = []
        for state in ("pending", "submitted", "graded"):
            merged.extend(self.homework(wlkcid, state, csrf=csrf))
        return merged

    def homework_detail(self, zyid: str, csrf: str = None) -> str:
        """作业描述(富文本 HTML)."""
        data = self._post_json(HOMEWORK_DETAIL_PATH, [("id", zyid)],
                               csrf=csrf)
        message = data.get("msg")
        if not isinstance(message, str):
            raise ProtocolChangedError("作业详情端点响应缺 msg")
        return message

    def homework_attachments(self, wlkcid: str, zyid: str,
                             csrf: str = None) -> list:
        """作业附件 id 列表(viewCj 页链接的 fileId, 按出现序; 无附件为空)."""
        response = self._fetch(self._url(
            f"{HOMEWORK_VIEW_PATH}?wlkcid={wlkcid}&zyid={zyid}", csrf))
        if not response.text.strip():
            raise ProtocolChangedError("作业查看页无内容")
        return extract_query_ids(response.text, "fileId")

    # -- 公告 --

    def notices(self, wlkcid: str, csrf: str = None) -> list:
        """某课公告列表(未过期+已过期合并)."""
        merged = []
        for scope in ("active", "expired"):
            data = self._post_json(NOTICE_PATHS[scope],
                                   [("wlkcid", wlkcid)], csrf=csrf)
            for row in json_rows(data, "aaData"):
                if not isinstance(row, dict) or not row.get("ggid"):
                    continue
                merged.append(LearnNotice(
                    wlkcid=wlkcid,
                    ggid=_scalar(row, "ggid"),
                    bt=_scalar(row, "bt"),
                    body_html=decode_notice_html(_scalar(row, "ggnr")),
                    fbsj=_scalar(row, "fbsj"),
                    jzsj=_scalar(row, "jzsj"),
                    sfyd=_scalar(row, "sfyd") == YES_MARK,
                    fjmc=_scalar(row, "fjmc"),
                ))
        return merged

    def notice_attachments(self, wlkcid: str, ggid: str,
                           csrf: str = None) -> list:
        """公告附件 id 列表(详情页链接的 wjid, 按出现序)."""
        response = self._fetch(self._url(
            f"{NOTICE_DETAIL_PATH}?wlkcid={wlkcid}&id={ggid}", csrf))
        return extract_query_ids(response.text, "wjid")

    # -- 课件 --

    def files(self, wlkcid: str, csrf: str = None) -> list:
        """某课课件列表(size=200 即全量)."""
        data = self._get_json(
            f"{FILES_PATH}?wlkcid={wlkcid}&size=200", csrf=csrf)
        rows = json_rows(data, "resultList", "aaData")
        items = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("kjxxid"):
                continue
            size = row.get("wjdx")
            if not isinstance(size, int):
                try:
                    size = int(str(size))
                except (TypeError, ValueError):
                    size = 0
            items.append(LearnFile(
                wlkcid=wlkcid,
                kjxxid=_scalar(row, "kjxxid"),
                wjid=_scalar(row, "wjid"),
                bt=_scalar(row, "bt"),
                ms=_scalar(row, "ms"),
                scsj=_scalar(row, "scsj"),
                wjdx=size,
                kjflid=_scalar(row, "kjflid"),
            ))
        return items

    def file_categories(self, wlkcid: str, csrf: str = None) -> dict:
        """课件分类 id -> 名称."""
        data = self._get_json(
            f"{FILE_CATEGORIES_PATH}?wlkcid={wlkcid}", csrf=csrf)
        rows = json_rows(data, "rows", "aaData")
        return {_scalar(row, "kjflid"): _scalar(row, "bt")
                for row in rows if isinstance(row, dict) and row.get("kjflid")}

    # -- 下载 --

    def download_bytes(self, path: str, csrf: str = None) -> bytes:
        """下载一个文件端点, 返回原始字节.

        响应为 HTML(登录页/错误页)时抛错, 防止把页面当文件存盘.
        """
        response = self._fetch(self._url(path, csrf))
        content_type = (response.header("Content-Type") or "").lower()
        head = response.content[:256].lstrip().lower()
        if "text/html" in content_type or head.startswith(b"<"):
            raise LearnError(
                "下载响应不是文件(可能是错误页), 请稍后重试")
        return response.content


def download_course_file_url(wjid: str) -> str:
    """课件下载路径(供 learn_sync 组装)."""
    return f"{FILE_DOWNLOAD_PATH}?sfgk=0&wjid={wjid}"


def download_homework_file_url(wlkcid: str, file_id: str) -> str:
    """作业附件下载路径(供 learn_sync 组装)."""
    return HOMEWORK_FILE_PATH.format(wlkcid=wlkcid, file_id=file_id)
