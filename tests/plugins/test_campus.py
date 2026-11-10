"""campus 插件测试: run(job) 三动作分派(假 transport, 零真实账号)."""

import json

import pytest

import shuimu.plugins.campus.plugin as campus_plugin
from shuimu.core.campus import (
    CHECK_URL,
    DOUBLE_AUTH_URL,
    ROAM_FORM_URL,
    SessionStore,
)
from shuimu.core.campus_net import MemoryTransport
from shuimu.core.pluginapi import PluginJob
from tests.core.test_campus import (
    CALLBACK_URL,
    LOGIN_PAGE,
    USER,
    add_login_prelude,
    add_roam_info_tail,
    success_page,
)

PASSWORD = "pw-secret"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """会话存储隔离到 tmp 并注入插件模块."""
    session = SessionStore(str(tmp_path / "campus-session.json"),
                           str(tmp_path / "campus-fingerprint"))
    monkeypatch.setattr(campus_plugin, "_store", lambda: session)
    return session


@pytest.fixture
def transport(monkeypatch):
    """假 transport 注入插件模块."""
    fake = MemoryTransport()
    monkeypatch.setattr(campus_plugin, "_transport_factory", lambda: fake)
    return fake


def run_plugin(**params):
    """以给定参数运行 campus 插件."""
    return campus_plugin.run(PluginJob(params=params))


def add_success_routes(transport):
    """凭据直登成功(无 2FA)的全部路由."""
    add_login_prelude(transport)
    transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
    transport.add("GET", CALLBACK_URL, text="webvpn ok")
    transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
    transport.add("POST", CHECK_URL, text=success_page(
        "https://info.tsinghua.edu.cn/f/info/gxfw_fg/common/index"))
    add_roam_info_tail(transport)


class TestStatus:
    """action=status 只读会话."""

    @pytest.mark.usefixtures("store")
    def test_not_logged_in(self):
        """无会话文件: 未登录."""
        report = run_plugin(action="status")
        assert report.ok
        assert report.summary == "未登录"

    def test_logged_in_summary(self, store, transport):
        """有会话: 摘要含学号与姓名, 不发任何请求."""
        store.save({"version": 1, "username": USER,
                    "profile": {"username": USER, "name": "张三", "raw": {}}})
        report = run_plugin(action="status")
        assert report.ok
        assert USER in report.summary and "张三" in report.summary
        assert not transport.requests


class TestLogin:
    """action=login."""

    def test_success_reports_profile(self, store, transport):
        """登录成功: 报告含本人信息, 会话落盘."""
        add_success_routes(transport)
        report = run_plugin(action="login", username=USER, password=PASSWORD)
        assert report.ok
        titles = [f.title for f in report.findings]
        assert any("张三" in title for title in titles)
        assert store.load()["username"] == USER

    @pytest.mark.usefixtures("store")
    def test_password_never_in_report(self, transport):
        """报告任何字段不含密码."""
        add_success_routes(transport)
        report = run_plugin(action="login", username=USER, password=PASSWORD)
        text = report.summary + "".join(
            f.title + f.detail for f in report.findings)
        assert PASSWORD not in text

    @pytest.mark.usefixtures("store")
    def test_bad_credentials(self, transport):
        """密码错误: 报告失败并带服务端文案."""
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL,
                      text='<span id="msg_note">用户名或密码错误</span>')
        report = run_plugin(action="login", username=USER, password="wrong")
        assert not report.ok
        assert "用户名或密码错误" in report.findings[0].title

    @pytest.mark.usefixtures("store", "transport")
    def test_missing_params(self):
        """缺学号或密码: 参数错误."""
        report = run_plugin(action="login", username=USER)
        assert not report.ok
        assert "参数不全" in report.summary

    @pytest.mark.usefixtures("store")
    def test_need_2fa_hint(self, transport):
        """需要二次认证且未预置 code: 报告可选方式与补参提示."""
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text="<div>登录需要二次认证</div>")
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success", "object": {"hasWeChatBool": False,
                                             "phone": "138****1234",
                                             "hasTotp": True}}))
        report = run_plugin(action="login", username=USER, password=PASSWORD)
        assert not report.ok
        details = " ".join(f.detail for f in report.findings)
        assert "短信" in details and "totp" in details

    def test_login_with_totp_code(self, store, transport):
        """预置 method+code(totp): 非交互完成二次认证."""
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text="<div>登录需要二次认证</div>")
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success", "object": {"hasWeChatBool": False,
                                             "phone": "",
                                             "hasTotp": True}}))
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success",
             "object": {"redirectUrl": "/do/off/ui/auth/login/finish"}}))
        transport.add(
            "GET", "https://id.tsinghua.edu.cn/do/off/ui/auth/login/finish",
            text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text=success_page(
            "https://info.tsinghua.edu.cn/f/info/gxfw_fg/common/index"))
        add_roam_info_tail(transport)
        report = run_plugin(action="login", username=USER,
                             password=PASSWORD, method="totp", code="654321")
        assert report.ok
        assert store.load()["username"] == USER


class TestLogout:
    """action=logout."""

    @pytest.mark.usefixtures("transport")
    def test_logout_clears_session_keeps_fingerprint(self, store):
        """登出清会话, 指纹保留."""
        fingerprint = store.fingerprint()
        store.save({"version": 1, "username": USER,
                    "profile": {}, "cookies": {}})
        report = run_plugin(action="logout")
        assert report.ok
        assert "已登出" in report.summary
        assert store.load() is None
        assert store.fingerprint() == fingerprint

    @pytest.mark.usefixtures("store", "transport")
    def test_logout_without_session(self):
        """本无会话时登出仍成功."""
        report = run_plugin(action="logout")
        assert report.ok
        assert "本无会话" in report.summary
