"""cloud 插件测试: run(job) 动作分派(假 transport + tmp 库, 零真实账号)."""

import pytest

import shuimu.plugins.cloud.plugin as cloud_plugin
from shuimu.core.campus_net import MemoryTransport
from shuimu.core.pluginapi import PluginJob
from shuimu.core.pluginman import PluginManager
from shuimu.core.storage import Storage
from tests.core.test_cloud import (
    SHARE_URL,
    add_dirents,
    add_share_page,
    dir_row,
)

TREE = {
    "/": [dir_row("专业核心课程")],
    "/专业核心课程/": [dir_row("核仪器原理"), dir_row("无关目录")],
}


@pytest.fixture
def transport(monkeypatch):
    """假 transport 注入插件模块."""
    fake = MemoryTransport()
    monkeypatch.setattr(cloud_plugin, "_transport_factory", lambda: fake)
    return fake


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """工作台数据库路径注入(插件每次自开自关, 测试自行打开断言)."""
    path = str(tmp_path / "cloud.db")
    monkeypatch.setattr(cloud_plugin, "_storage", lambda: Storage(path))
    return path


def run_plugin(**params):
    """以给定参数运行 cloud 插件."""
    return cloud_plugin.run(PluginJob(params=params))


def seed_tree(transport):
    """登记两级目录树路由."""
    add_share_page(transport, title="多课程资料库")
    for path, rows in TREE.items():
        add_dirents(transport, rows, path=None if path == "/" else path)


class TestBrowse:
    """action=browse 只读预览."""

    def test_browse_matched_and_unmatched(self, transport, db_path):
        """匹配目录 ok finding, 未匹配 warn finding."""
        storage = Storage(db_path)
        try:
            storage.add_course("核仪器原理")
        finally:
            storage.close()
        seed_tree(transport)
        report = run_plugin(action="browse", url=SHARE_URL)
        assert report.ok
        assert "多课程资料库" in report.summary
        assert any("核仪器原理" in f.title for f in report.findings)
        assert any("未匹配" in f.title for f in report.findings)

    @pytest.mark.usefixtures("db_path")
    def test_browse_bad_url(self, transport):
        """非法链接: error finding, 零请求."""
        report = run_plugin(action="browse", url="https://example.com/")
        assert not report.ok
        assert report.findings[0].severity == "error"
        assert transport.requests == []


class TestMount:
    """action=mount 挂载登记."""

    def test_mount_registers(self, transport, db_path):
        """挂载: 资源入库 + ok finding 带资源 id."""
        storage = Storage(db_path)
        try:
            course = storage.add_course("核仪器原理")
        finally:
            storage.close()
        seed_tree(transport)
        report = run_plugin(action="mount", url=SHARE_URL)
        assert report.ok
        assert "新挂载 1" in report.summary
        assert any("已挂载 核仪器原理" in f.title for f in report.findings)
        storage = Storage(db_path)
        try:
            resources = storage.list_resources(course)
            assert len(resources) == 1
            assert resources[0]["display_name"] == "核仪器原理"
        finally:
            storage.close()

    def test_mount_twice_skipped(self, transport, db_path):
        """重复挂载: skipped finding, 不重复登记."""
        storage = Storage(db_path)
        try:
            course = storage.add_course("核仪器原理")
        finally:
            storage.close()
        seed_tree(transport)
        assert run_plugin(action="mount", url=SHARE_URL).ok
        transport._routes.clear()   # pylint: disable=protected-access
        transport.requests.clear()
        seed_tree(transport)
        report = run_plugin(action="mount", url=SHARE_URL)
        assert "新挂载 0" in report.summary
        assert any("已挂载过" in f.title for f in report.findings)
        storage = Storage(db_path)
        try:
            assert len(storage.list_resources(course)) == 1
        finally:
            storage.close()


class TestManifest:
    """插件清单可被框架加载."""

    def test_builtin_discovery(self, tmp_path):
        """内置插件目录发现 cloud 且 manifest 合法(状态文件隔离)."""
        manager = PluginManager(state_path=str(tmp_path / "state.yaml"))
        record = manager.get("cloud")
        assert record is not None
        assert record.status == "disabled"
        assert record.manifest.name == "清华云盘"
        assert "network" in record.manifest.permissions
