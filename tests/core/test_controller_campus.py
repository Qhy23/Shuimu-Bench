"""controller 校园动作测试: 登录/状态/登出(假 transport, 零真实账号)."""

import json

import pytest

from shuimu.core.campus import (
    CHECK_URL,
    GRJBXX_URL,
    ROAM_FORM_URL,
    XSRF_URL,
)
from shuimu.core.campus_net import MemoryTransport, TransportError
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage
from tests.core.test_campus import (
    CALLBACK_URL,
    LOGIN_PAGE,
    WEBVPN_LOGIN_URL,
    add_login_prelude,
    add_roam_info_tail,
    success_page,
)

USERNAME = "20231234567"
PASSWORD = "pw-secret"


@pytest.fixture()
def ctl_campus(tmp_path, monkeypatch):
    """校园路径与传输层全隔离的 controller: 返回 (controller, transport).

    campus 插件在隔离状态文件中预先启用(network 授权).
    """
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-such-links-template.yaml"))
    monkeypatch.setattr(
        "shuimu.core.controller.default_campus_fingerprint_path",
        lambda: str(tmp_path / "campus-fingerprint"))
    transport = MemoryTransport()
    storage = Storage(str(tmp_path / "ctl.db"))
    controller = Controller(
        storage, schedule_path=str(tmp_path / "s.yaml"),
        links_path=str(tmp_path / "links.yaml"),
        plugins_state_path=str(tmp_path / "plugins.yaml"),
        external_plugins_dir=str(tmp_path / "ext-plugins"),
        campus_session_path=str(tmp_path / "campus-session.json"),
        campus_transport_factory=lambda: transport)
    controller.plugins.set_enabled("campus", True)
    yield controller, transport
    storage.close()


class TestCampusActions:
    """校园登录/状态/登出动作."""

    def _login_routes(self, transport):
        """凭据直登成功路由."""
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text=success_page(
            "https://info.tsinghua.edu.cn/f/info/gxfw_fg/common/index"))
        add_roam_info_tail(transport)

    def test_login_success_logs_without_password(self, ctl_campus):
        """成功登录留痕; 留痕全文不含密码."""
        ctl, transport = ctl_campus
        self._login_routes(transport)
        result = ctl.campus_login("20231234567", "pw-secret")
        assert result.ok and "张三" in result.message
        row = ctl.storage.list_operations(1)[0]
        assert row["action"] == "campus_login"
        assert row["status"] == "ok"
        assert "pw-secret" not in f"{row['params']}{row['detail']}"
        assert json.loads(row["detail"])["name"] == "张三"

    def test_login_blocked_when_plugin_disabled(self, tmp_path, monkeypatch):
        """插件未启用: 拒绝且零请求零留痕."""
        monkeypatch.setattr("shuimu.core.controller.template_links_path",
                            lambda: str(tmp_path / "no-such-links-template.yaml"))
        monkeypatch.setattr(
            "shuimu.core.controller.default_campus_fingerprint_path",
            lambda: str(tmp_path / "campus-fingerprint"))
        transport = MemoryTransport()
        storage = Storage(str(tmp_path / "ctl.db"))
        controller = Controller(
            storage, schedule_path=str(tmp_path / "s.yaml"),
            links_path=str(tmp_path / "links.yaml"),
            plugins_state_path=str(tmp_path / "plugins.yaml"),
            external_plugins_dir=str(tmp_path / "ext-plugins"),
            campus_session_path=str(tmp_path / "campus-session.json"),
            campus_transport_factory=lambda: transport)
        result = controller.campus_login("20231234567", "pw")
        assert not result.ok and "未启用" in result.message
        assert not transport.requests
        assert not storage.list_operations(200)
        storage.close()

    def test_login_bad_credentials_logs_failed(self, ctl_campus):
        """密码错误: 留痕 failed 带服务端文案."""
        ctl, transport = ctl_campus
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL,
                      text='<span id="msg_note">用户名或密码错误</span>')
        result = ctl.campus_login("20231234567", "wrong")
        assert not result.ok
        row = ctl.storage.list_operations(1)[0]
        assert row["status"] == "failed"
        assert json.loads(row["detail"])["state"] == "bad_credentials"

    def test_status_cached_then_checked(self, ctl_campus):
        """无 check 零请求; check 联网校验刷新本人信息."""
        ctl, transport = ctl_campus
        self._login_routes(transport)
        ctl.campus_login("20231234567", "pw")
        transport.requests.clear()
        result = ctl.campus_status()
        assert result.ok and result.detail["state"] == "cached"
        assert not transport.requests
        result = ctl.campus_status(check=True)
        assert result.ok and result.detail["state"] == "valid"
        assert any("grjbxx" in r[1] for r in transport.requests)

    def test_status_expired(self, ctl_campus):
        """会话失效: 提示重新登录."""
        ctl, transport = ctl_campus
        self._login_routes(transport)
        ctl.campus_login("20231234567", "pw")
        transport.requests.clear()
        transport.add("GET", XSRF_URL, text="XSRF-TOKEN=tok;")
        transport.add("GET", GRJBXX_URL + "?_csrf=tok", status=302,
                      location=WEBVPN_LOGIN_URL)
        transport.add("GET", WEBVPN_LOGIN_URL, text="login")
        result = ctl.campus_status(check=True)
        assert not result.ok and result.detail["state"] == "expired"

    def test_status_network_error_keeps_session(self, ctl_campus):
        """网络异常: 会话保留, 只提示暂时无法校验."""
        ctl, transport = ctl_campus
        self._login_routes(transport)
        ctl.campus_login("20231234567", "pw")

        def broken_factory():
            class _Broken:
                def fetch(self, *_args, **_kwargs):
                    """恒网络故障."""
                    raise TransportError("dns fail")

            return _Broken()

        original = ctl._campus_transport_factory  # pylint: disable=protected-access
        ctl._campus_transport_factory = broken_factory  # pylint: disable=protected-access
        try:
            result = ctl.campus_status(check=True)
        finally:
            ctl._campus_transport_factory = original  # pylint: disable=protected-access
        assert not result.ok
        assert result.detail["state"] == "network_error"
        assert ctl._campus_store().load() is not None  # pylint: disable=protected-access

    def test_status_not_logged_in(self, ctl_campus):
        """无会话: 未登录."""
        ctl, _transport = ctl_campus
        result = ctl.campus_status()
        assert not result.ok and "未登录" in result.message

    def test_logout_roundtrip(self, ctl_campus):
        """登出清会话留痕; 再次登出提示本无会话."""
        ctl, transport = ctl_campus
        self._login_routes(transport)
        ctl.campus_login("20231234567", "pw")
        result = ctl.campus_logout()
        assert result.ok and "已登出" in result.message
        assert ctl._campus_store().load() is None  # pylint: disable=protected-access
        actions = [r["action"] for r in ctl.storage.list_operations(200)]
        assert "campus_logout" in actions
        again = ctl.campus_logout()
        assert again.ok and "本无会话" in again.message
