"""learn 插件测试: run(job) 动作分派(假 transport, 零真实账号)."""

import json
import os

import pytest

import shuimu.plugins.learn.plugin as learn_plugin
from shuimu.core.campus import SessionStore
from shuimu.core.campus_net import MemoryTransport
from shuimu.core.learn import (
    LearnCourse,
    LearnFile,
    LearnHomework,
    download_course_file_url,
    learn_url,
)
from shuimu.core.pluginapi import PluginJob
from shuimu.core.storage import Storage
from tests.core.test_learn import (
    INFO_XSRF_URL,
    INDEX_PAGE_PATH,
    SEMESTER,
    WLKCID,
    add_csrf_route,
    add_download_route,
)
from tests.core.test_learn_sync import (
    COURSE_A,
    add_course_detail_routes,
    add_refresh_prelude,
)


@pytest.fixture
def session_path(tmp_path, monkeypatch):
    """会话存储隔离到 tmp 并注入插件模块; 返回会话文件路径."""
    path = str(tmp_path / "campus-session.json")
    store = SessionStore(path, str(tmp_path / "campus-fingerprint"))
    store.save({"version": 1, "username": "20231234567",
                "cookies": {"webvpn.tsinghua.edu.cn":
                            {"wengine_vpn_ticket": "t1"}},
                "profile": {}, "checked_at": 0, "saved_at": 0})
    monkeypatch.setattr(learn_plugin, "_session_store",
                        lambda: SessionStore(
                            path, str(tmp_path / "campus-fingerprint")))
    return path


@pytest.fixture
def transport(monkeypatch):
    """假 transport 注入插件模块."""
    fake = MemoryTransport()
    monkeypatch.setattr(learn_plugin, "_transport_factory", lambda: fake)
    return fake


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """工作台数据库路径注入(插件每次自开自关, 测试自行打开断言)."""
    path = str(tmp_path / "plugin.db")
    monkeypatch.setattr(learn_plugin, "_storage", lambda: Storage(path))
    return path


def run_plugin(**params):
    """以给定参数运行 learn 插件."""
    return learn_plugin.run(PluginJob(params=params))


def seed(db_path):
    """预置缓存: 一门课 + 一份未交作业 + 一个课件, 返回本地课程 id."""
    storage = Storage(db_path)
    try:
        local = storage.add_course("AI 编程")
        storage.learn_sync_courses(
            SEMESTER, [LearnCourse(**COURSE_A)])
        storage.learn_sync_homework(
            [LearnHomework(wlkcid=WLKCID, zyid="Z1", state="pending",
                           bt="作业一")])
        storage.learn_sync_files(
            [LearnFile(wlkcid=WLKCID, kjxxid="K1", wjid="WJ1", bt="讲义.pdf",
                       wjdx=9)])
        storage.learn_set_course_link(WLKCID, local)
        return local
    finally:
        storage.close()


class TestStatus:
    """action=status 只读缓存."""

    def test_status_with_cache(self, db_path):
        """缓存计数与登录态(零联网)."""
        seed(db_path)
        report = run_plugin(action="status")
        assert report.ok
        assert "1 课" in report.summary and "未交 1" in report.summary

    @pytest.mark.usefixtures("db_path")
    def test_status_empty(self):
        """空缓存也正常."""
        report = run_plugin(action="status")
        assert report.ok
        assert "0 课" in report.summary


class TestRefresh:
    """action=refresh 全量刷新."""

    @pytest.mark.usefixtures("db_path")
    def test_refresh_findings(self, session_path, transport):
        """成功: 新作业转 ok finding, 会话回写."""
        add_refresh_prelude(transport)
        add_course_detail_routes(transport, WLKCID,
                                 homework=[{"zyid": "Z1", "bt": "作业一"}])
        add_course_detail_routes(transport, "2025-2026-1-00000002")
        report = run_plugin(action="refresh")
        assert report.ok
        assert any(f.check == "homework" and f.severity == "ok"
                   and "作业一" in f.title for f in report.findings)
        with open(session_path, encoding="utf-8") as f:
            assert json.load(f)["saved_at"] > 0

    @pytest.mark.usefixtures("transport", "db_path")
    def test_refresh_not_logged_in(self, session_path):
        """无会话: error finding + 重登提示."""
        os.remove(session_path)
        report = run_plugin(action="refresh")
        assert not report.ok
        assert any(f.severity == "error" and "未登录" in f.title
                   for f in report.findings)

    @pytest.mark.usefixtures("session_path", "db_path")
    def test_refresh_expired(self, transport):
        """学堂会话失效且漫游不可用: 提示重登."""
        transport.add("GET", learn_url(INDEX_PAGE_PATH),
                      text="<html>您未登录或登录失效</html>")
        transport.add("GET", INFO_XSRF_URL, status=403)
        report = run_plugin(action="refresh")
        assert not report.ok
        assert any(f.severity == "error" and "重新登录" in f.detail
                   for f in report.findings)


class TestDownload:
    """action=download 下载."""

    def _routes(self, transport):
        add_csrf_route(transport)
        add_download_route(transport, download_course_file_url("WJ1"),
                           b"pdf-bytes")

    @pytest.mark.usefixtures("session_path")
    def test_download_ok_registers(self, transport, db_path, tmp_path):
        """下载成功并登记进关联课程."""
        local = seed(db_path)
        self._routes(transport)
        report = run_plugin(action="download", kind="file", file_id="WJ1",
                            out_dir=str(tmp_path / "out"))
        assert report.ok
        storage = Storage(db_path)
        try:
            resources = storage.list_resources(local)
        finally:
            storage.close()
        assert resources and resources[0]["path_or_url"].endswith("讲义.pdf")

    @pytest.mark.usefixtures("session_path", "transport", "db_path")
    def test_download_missing_file_id(self):
        """缺 file_id: 参数错误."""
        report = run_plugin(action="download", kind="file")
        assert not report.ok
        assert any(f.check == "input" for f in report.findings)

    @pytest.mark.usefixtures("session_path", "transport", "db_path")
    def test_download_homework_needs_wlkcid(self):
        """作业附件缺 wlkcid: 参数错误."""
        report = run_plugin(action="download", kind="homework",
                            file_id="Z1")
        assert not report.ok

    @pytest.mark.usefixtures("session_path", "transport", "db_path")
    def test_download_unknown_file(self):
        """课件不在缓存: 提示先 refresh."""
        report = run_plugin(action="download", kind="file", file_id="NOPE")
        assert not report.ok
        assert any("找不到" in f.title for f in report.findings)


class TestTodos:
    """action=todos 待办同步."""

    @pytest.mark.usefixtures("session_path", "transport")
    def test_todos_syncs(self, db_path, tmp_path, monkeypatch):
        """同步未交作业并汇总."""
        sched = str(tmp_path / "s.yaml")
        monkeypatch.setattr(learn_plugin, "default_schedule_path",
                            lambda: sched)
        seed(db_path)
        report = run_plugin(action="todos")
        assert report.ok
        assert "新增 1" in report.summary
        with open(sched, encoding="utf-8") as f:
            assert "[学堂] 作业一" in f.read()
