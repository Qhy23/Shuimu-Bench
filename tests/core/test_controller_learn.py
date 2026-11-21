"""controller 学堂动作测试: 门禁/刷新/下载/详情/关联(假 transport, 零真实账号)."""

import json
import os

import pytest

from shuimu.core.campus_net import MemoryTransport
from shuimu.core.controller import Controller
from shuimu.core.learn import (
    HOMEWORK_DETAIL_PATH,
    LearnCourse,
    LearnFile,
    LearnHomework,
    download_course_file_url,
    learn_url,
)
from shuimu.core.schedule import load_schedule
from shuimu.core.storage import Storage
from tests.core.test_learn import (
    INFO_XSRF_URL,
    INDEX_PAGE_PATH,
    SEMESTER,
    WLKCID,
    _post_url,
    add_csrf_route,
    add_download_route,
)
from tests.core.test_learn_sync import (
    COURSE_A,
    add_course_detail_routes,
    add_refresh_prelude,
)

USERNAME = "20231234567"
DOWNLOAD_DIR_NAME = "downloads"


def write_session(tmp_path):
    """预写校园会话文件, 返回路径."""
    path = tmp_path / "campus-session.json"
    path.write_text(json.dumps({
        "version": 1, "username": USERNAME,
        "cookies": {"webvpn.tsinghua.edu.cn":
                    {"wengine_vpn_ticket": "t1"}},
        "profile": {"username": USERNAME, "name": "张三", "raw": {}},
        "checked_at": 0, "saved_at": 0}), encoding="utf-8")
    return str(path)


def write_stub_plugin(tmp_path):
    """外部目录放最小 learn 替身插件(controller 层测试与插件实现解耦)."""
    plugin_dir = tmp_path / "ext-plugins" / "learn"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yaml").write_text(
        "id: learn\nname: 学堂替身\nversion: \"0.1.0\"\napi_version: 1\n"
        "description: controller 测试替身\nentry: plugin\n"
        "permissions: [network]\napplies_to: [app]\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "\"\"\"替身: controller 测试不跑插件入口.\"\"\"\n\n\n"
        "def run(job):\n    raise NotImplementedError\n",
        encoding="utf-8")


@pytest.fixture()
def ctl_learn(tmp_path, monkeypatch):
    """学堂动作全隔离的 controller: 返回 (controller, transport, tmp_path).

    会话文件预写(有效 webvpn cookie); 外部目录放 learn 替身插件且默认
    未启用(门禁用例需要), 其余用例自行调用 enable().
    """
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-such-template.yaml"))
    monkeypatch.setattr(
        "shuimu.core.controller.default_campus_fingerprint_path",
        lambda: str(tmp_path / "campus-fingerprint"))
    write_stub_plugin(tmp_path)
    transport = MemoryTransport()
    storage = Storage(str(tmp_path / "ctl.db"))
    controller = Controller(
        storage, schedule_path=str(tmp_path / "s.yaml"),
        links_path=str(tmp_path / "links.yaml"),
        plugins_state_path=str(tmp_path / "plugins.yaml"),
        external_plugins_dir=str(tmp_path / "ext-plugins"),
        campus_session_path=write_session(tmp_path),
        campus_transport_factory=lambda: transport)
    yield controller, transport, tmp_path
    storage.close()


def enable_learn(controller):
    """启用 learn 插件(network 授权)."""
    controller.plugins.set_enabled("learn", True)


def seed_cache(controller, link_course=None):
    """预置学堂缓存: 一门课 + 一份作业 + 一个课件."""
    controller.storage.learn_sync_courses(
        SEMESTER, [LearnCourse(**COURSE_A)])
    controller.storage.learn_sync_homework(
        [LearnHomework(wlkcid=WLKCID, zyid="Z1", state="pending",
                       bt="作业一")])
    controller.storage.learn_sync_files(
        [LearnFile(wlkcid=WLKCID, kjxxid="K1", wjid="WJ1", bt="讲义一.pdf",
                   wjdx=12)])
    if link_course is not None:
        controller.storage.learn_set_course_link(WLKCID, link_course)


class TestLearnGate:
    """门禁与未登录."""

    def test_disabled_plugin_no_requests(self, ctl_learn):
        """未启用: 失败且零联网零留痕."""
        controller, transport, _ = ctl_learn
        result = controller.learn_refresh()
        assert not result.ok
        assert "未启用" in result.message
        assert transport.requests == []
        assert all(row["action"] != "learn_refresh"
                   for row in controller.storage.list_operations())

    def test_not_logged_in(self, ctl_learn, tmp_path):
        """无会话文件: logged_out."""
        controller, _, _ = ctl_learn
        os.remove(tmp_path / "campus-session.json")
        enable_learn(controller)
        result = controller.learn_refresh()
        assert not result.ok
        assert result.detail["state"] == "logged_out"


class TestLearnRefresh:
    """全量刷新."""

    def test_refresh_ok_logs_and_saves_session(self, ctl_learn):
        """成功: 计数入留痕, 会话 cookie 回写."""
        controller, transport, tmp_path = ctl_learn
        enable_learn(controller)
        add_refresh_prelude(transport)
        add_course_detail_routes(transport, WLKCID,
                                 homework=[{"zyid": "Z1", "bt": "作业一"}])
        add_course_detail_routes(transport, "2025-2026-1-00000002")
        result = controller.learn_refresh()
        assert result.ok
        assert result.detail["courses_new"] == 2
        assert result.detail["homework_new"] == 1
        rows = [row for row in controller.storage.list_operations()
                if row["action"] == "learn_refresh"]
        assert len(rows) == 1 and rows[0]["status"] == "ok"
        with open(tmp_path / "campus-session.json", encoding="utf-8") as f:
            assert json.load(f)["saved_at"] > 0

    def test_refresh_expired(self, ctl_learn):
        """学堂与门户会话都失效: expired 状态."""
        controller, transport, _ = ctl_learn
        enable_learn(controller)
        transport.add("GET", learn_url(INDEX_PAGE_PATH),
                      text="<html>您未登录或登录失效</html>")
        transport.add("GET", INFO_XSRF_URL, status=403)
        result = controller.learn_refresh()
        assert not result.ok
        assert result.detail["state"] == "expired"


class TestLearnStatus:
    """只读状态."""

    def test_status_offline(self, ctl_learn):
        """零联网, 缓存计数与上次刷新时间."""
        controller, transport, _ = ctl_learn
        seed_cache(controller)
        result = controller.learn_status()
        assert result.ok
        assert result.detail["courses"] == 1
        assert result.detail["homework"] == 1
        assert transport.requests == []       # 零联网


class TestLearnDownload:
    """下载落盘登记."""

    def _download_routes(self, transport):
        add_csrf_route(transport)
        add_download_route(transport, download_course_file_url("WJ1"),
                           b"%PDF-fake-bytes")

    def test_download_registers_resource(self, ctl_learn):
        """下载: 落盘课名子目录, 登记进关联课程, 留痕."""
        controller, transport, tmp_path = ctl_learn
        enable_learn(controller)
        local = controller.storage.add_course("AI 编程")
        seed_cache(controller, link_course=local)
        self._download_routes(transport)
        out = str(tmp_path / DOWNLOAD_DIR_NAME)
        result = controller.learn_download(
            "file", "WJ1", out_dir=out)
        assert result.ok and not result.detail["skipped"]
        path = result.detail["path"]
        assert path == os.path.join(out, "AI 编程", "讲义一.pdf")
        with open(path, "rb") as f:
            assert f.read() == b"%PDF-fake-bytes"
        assert result.detail["resource_id"] is not None
        resources = controller.storage.list_resources(local)
        assert [row["path_or_url"] for row in resources] == [path]
        assert any(row["action"] == "learn_download"
                   for row in controller.storage.list_operations())

    def test_download_skips_existing(self, ctl_learn):
        """已下载且文件在: 跳过不重复请求."""
        controller, transport, tmp_path = ctl_learn
        enable_learn(controller)
        local = controller.storage.add_course("AI 编程")
        seed_cache(controller, link_course=local)
        self._download_routes(transport)
        out = str(tmp_path / DOWNLOAD_DIR_NAME)
        first = controller.learn_download("file", "WJ1", out_dir=out)
        assert first.ok and not first.detail["skipped"]
        count = len(transport.requests)
        second = controller.learn_download("file", "WJ1", out_dir=out)
        assert second.ok and second.detail["skipped"]
        assert second.detail["path"] == first.detail["path"]
        assert len(transport.requests) == count  # 跳过不重复请求

    def test_download_unlinked_course_no_resource(self, ctl_learn):
        """未关联课程: 仍落盘但不登记 resources."""
        controller, transport, tmp_path = ctl_learn
        enable_learn(controller)
        seed_cache(controller)                     # link_course=None
        self._download_routes(transport)
        result = controller.learn_download(
            "file", "WJ1", out_dir=str(tmp_path / DOWNLOAD_DIR_NAME))
        assert result.ok
        assert result.detail["resource_id"] is None
        assert os.path.exists(result.detail["path"])

    def test_download_unknown_file(self, ctl_learn):
        """缓存中没有该文件: 参数错误失败, 零联网."""
        controller, transport, _ = ctl_learn
        enable_learn(controller)
        result = controller.learn_download("file", "NOPE")
        assert not result.ok and "找不到" in result.message
        assert transport.requests == []


class TestHomeworkDetail:
    """作业描述缓存."""

    def test_detail_cached_no_network(self, ctl_learn):
        """缓存命中: 零联网返回."""
        controller, transport, _ = ctl_learn
        seed_cache(controller)
        controller.storage.learn_save_homework_detail(
            WLKCID, "Z1", "<p>desc</p>")
        enable_learn(controller)
        result = controller.learn_homework_detail(WLKCID, "Z1")
        assert result.ok and result.detail["cached"]
        assert result.detail["html"] == "<p>desc</p>"
        assert transport.requests == []

    def test_detail_fetches_and_caches(self, ctl_learn):
        """缓存缺失: 拉取后回填, 二次零联网."""
        controller, transport, _ = ctl_learn
        seed_cache(controller)
        enable_learn(controller)
        add_csrf_route(transport)
        transport.add("POST", _post_url(HOMEWORK_DETAIL_PATH),
                      text=json.dumps({"msg": "<p>new</p>"}))
        result = controller.learn_homework_detail(WLKCID, "Z1")
        assert result.ok and not result.detail["cached"]
        assert result.detail["html"] == "<p>new</p>"
        second = controller.learn_homework_detail(WLKCID, "Z1")
        assert second.detail["cached"]
        assert len(transport.requests) == 2     # 仅首页 + 首次详情


class TestCourseLink:
    """手动关联."""

    def test_link_and_unlink(self, ctl_learn):
        """关联本地课程与取消关联."""
        controller, _, _ = ctl_learn
        seed_cache(controller)
        local = controller.storage.add_course("本地课")
        result = controller.learn_set_course_link(WLKCID, local)
        assert result.ok
        assert any(row["action"] == "learn_link_course"
                   for row in controller.storage.list_operations())
        result = controller.learn_set_course_link(WLKCID, None)
        assert result.ok and "取消" in result.message

    def test_link_unknown_course(self, ctl_learn):
        """本地课程不存在: 失败."""
        controller, _, _ = ctl_learn
        seed_cache(controller)
        result = controller.learn_set_course_link(WLKCID, 999)
        assert not result.ok


class TestSyncTodos:
    """待办同步动作与刷新串联."""

    def test_sync_todos_action(self, ctl_learn):
        """动作同步 + 留痕."""
        controller, _transport, _tmp = ctl_learn
        enable_learn(controller)
        seed_cache(controller)
        result = controller.learn_sync_todos()
        assert result.ok and result.detail["added"] == 1
        todos = load_schedule(controller.schedule_path).todos
        assert any(t.title == "[学堂] 作业一" for t in todos)
        assert any(row["action"] == "learn_sync_todos"
                   for row in controller.storage.list_operations())

    def test_refresh_chains_todos(self, ctl_learn):
        """刷新出新作业后默认串联同步."""
        controller, transport, _tmp = ctl_learn
        enable_learn(controller)
        add_refresh_prelude(transport)
        add_course_detail_routes(
            transport, WLKCID, homework=[{"zyid": "Z1", "bt": "作业一"}])
        add_course_detail_routes(transport, "2025-2026-1-00000002")
        result = controller.learn_refresh()
        assert result.ok and "待办同步" in result.message
        todos = load_schedule(controller.schedule_path).todos
        assert any(t.title == "[学堂] 作业一" for t in todos)

    def test_refresh_without_todos(self, ctl_learn):
        """sync_todos=False 不写日程."""
        controller, transport, _tmp = ctl_learn
        enable_learn(controller)
        add_refresh_prelude(transport)
        add_course_detail_routes(
            transport, WLKCID, homework=[{"zyid": "Z1", "bt": "作业一"}])
        add_course_detail_routes(transport, "2025-2026-1-00000002")
        result = controller.learn_refresh(sync_todos=False)
        assert result.ok
        assert not load_schedule(controller.schedule_path).todos
