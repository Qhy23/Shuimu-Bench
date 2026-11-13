"""controller 云盘动作测试: 门禁/浏览/挂载(假 transport, 零真实账号)."""

import json

import pytest

from shuimu.core.campus_net import MemoryTransport, TransportError
from shuimu.core.controller import Controller
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


class FlakyTransport(MemoryTransport):
    """全部请求抛 TransportError 的假 transport."""

    def fetch(self, url, method="GET", body=None, headers=None):
        raise TransportError(f"boom: {method} {url}")


def write_stub_plugin(tmp_path):
    """外部目录放最小 cloud 替身插件(controller 层测试与插件实现解耦)."""
    plugin_dir = tmp_path / "ext-plugins" / "cloud"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yaml").write_text(
        "id: cloud\nname: 云盘替身\nversion: \"0.1.0\"\napi_version: 1\n"
        "description: controller 测试替身\nentry: plugin\n"
        "permissions: [network]\napplies_to: [app]\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "\"\"\"替身: controller 测试不跑插件入口.\"\"\"\n\n\n"
        "def run(job):\n    raise NotImplementedError\n",
        encoding="utf-8")


def seed_tree(transport):
    """登记两级目录树路由."""
    add_share_page(transport, title="多课程资料库")
    for path, rows in TREE.items():
        add_dirents(transport, rows, path=None if path == "/" else path)


@pytest.fixture()
def ctl_cloud(tmp_path, monkeypatch):
    """云盘动作全隔离的 controller: 返回 (controller, transport, tmp_path).

    外部目录放 cloud 替身插件且默认未启用(门禁用例需要), 其余用例自行
    调用 enable().
    """
    monkeypatch.setattr("shuimu.core.controller.template_schedule_path",
                        lambda: str(tmp_path / "no-such-template.yaml"))
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
        campus_session_path=str(tmp_path / "campus-session.json"),
        campus_transport_factory=lambda: transport)
    yield controller, transport, tmp_path
    storage.close()


def enable_cloud(controller):
    """启用 cloud 插件(network 授权)."""
    controller.plugins.set_enabled("cloud", True)


def seed_courses(controller):
    """预置与目录同名的本地课程, 返回其 id."""
    return controller.storage.add_course("核仪器原理")


class TestCloudGate:
    """门禁."""

    def test_plugin_missing(self, ctl_cloud, monkeypatch):
        """未安装: 失败且零联网零留痕."""
        controller, transport, _ = ctl_cloud
        monkeypatch.setattr(controller.plugins, "get", lambda _pid: None)
        result = controller.cloud_browse(SHARE_URL)
        assert not result.ok
        assert "未安装" in result.message
        assert transport.requests == []

    def test_disabled_no_requests_no_trace(self, ctl_cloud):
        """未启用: 失败且零联网零留痕."""
        controller, transport, _ = ctl_cloud
        result = controller.cloud_mount(SHARE_URL)
        assert not result.ok
        assert "未启用" in result.message
        assert transport.requests == []
        assert all(row["action"] != "cloud_mount"
                   for row in controller.storage.list_operations())


class TestCloudBrowse:
    """浏览(只读, 不留痕)."""

    def test_browse_ok(self, ctl_cloud):
        """成功: 匹配明细入 detail, 零留痕."""
        controller, transport, _ = ctl_cloud
        enable_cloud(controller)
        seed_courses(controller)
        seed_tree(transport)
        result = controller.cloud_browse(SHARE_URL)
        assert result.ok
        assert "多课程资料库" in result.message
        assert "匹配到课程 1 个" in result.message
        detail = result.detail
        assert detail["share"] == "多课程资料库"
        assert detail["token"] == SHARE_URL.rstrip("/").rsplit("/", 1)[-1]
        assert [m["name"] for m in detail["mounts"]] == ["核仪器原理"]
        assert detail["unmatched"] == ["专业核心课程", "无关目录"]
        assert all(row["action"] != "cloud_mount"
                   for row in controller.storage.list_operations())

    def test_browse_invalid_url_no_requests(self, ctl_cloud):
        """非法链接: 本地解析失败, 零请求."""
        controller, transport, _ = ctl_cloud
        enable_cloud(controller)
        result = controller.cloud_browse("https://example.com/d/x/")
        assert not result.ok
        assert "不是清华云盘" in result.message
        assert transport.requests == []

    def test_browse_network_error(self, ctl_cloud):
        """网络异常: network_error 状态."""
        controller, _transport, _ = ctl_cloud
        enable_cloud(controller)

        def flaky_factory():
            return FlakyTransport()

        controller._campus_transport_factory = flaky_factory
        result = controller.cloud_browse(SHARE_URL)
        assert not result.ok
        assert result.detail["state"] == "network_error"

    def test_browse_404(self, ctl_cloud):
        """链接失效: cloud_error 状态."""
        controller, transport, _ = ctl_cloud
        enable_cloud(controller)
        add_share_page(transport, status=404)
        result = controller.cloud_browse(SHARE_URL)
        assert not result.ok
        assert result.detail["state"] == "cloud_error"
        assert "不存在或已过期" in result.message


class TestCloudMount:
    """挂载(登记 + 留痕)."""

    def test_mount_registers_and_traces(self, ctl_cloud):
        """成功: 资源登记(显示名为目录名) + cloud_mount 留痕."""
        controller, transport, _ = ctl_cloud
        enable_cloud(controller)
        course_id = seed_courses(controller)
        seed_tree(transport)
        result = controller.cloud_mount(SHARE_URL)
        assert result.ok
        assert "新挂载 1" in result.message
        resources = controller.storage.list_resources(course_id)
        assert len(resources) == 1
        assert resources[0]["display_name"] == "核仪器原理"
        assert resources[0]["kind"] == "link"
        ops = [row for row in controller.storage.list_operations()
               if row["action"] == "cloud_mount"]
        assert len(ops) == 1
        params = json.loads(ops[0]["params"])
        assert params["url"] == SHARE_URL
        detail = json.loads(ops[0]["detail"])
        assert detail["mounted"][0]["dir"] == "核仪器原理"

    def test_mount_twice_idempotent(self, ctl_cloud):
        """重复挂载: 第二次全部 skipped, 不重复登记."""
        controller, transport, _ = ctl_cloud
        enable_cloud(controller)
        course_id = seed_courses(controller)
        seed_tree(transport)
        assert controller.cloud_mount(SHARE_URL).ok
        transport2 = MemoryTransport()
        seed_tree(transport2)

        def second_factory():
            return transport2

        controller._campus_transport_factory = second_factory
        result = controller.cloud_mount(SHARE_URL)
        assert result.ok
        assert "新挂载 0" in result.message
        assert "已挂载 1" in result.message
        assert len(controller.storage.list_resources(course_id)) == 1
        ops = [row for row in controller.storage.list_operations()
               if row["action"] == "cloud_mount"]
        assert len(ops) == 2

    def test_mount_network_error_no_trace(self, ctl_cloud):
        """网络异常: 失败且不留痕."""
        controller, _transport, _ = ctl_cloud
        enable_cloud(controller)

        def flaky_factory():
            return FlakyTransport()

        controller._campus_transport_factory = flaky_factory
        result = controller.cloud_mount(SHARE_URL)
        assert not result.ok
        assert result.detail["state"] == "network_error"
        assert all(row["action"] != "cloud_mount"
                   for row in controller.storage.list_operations())
