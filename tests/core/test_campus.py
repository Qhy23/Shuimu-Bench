"""campus.py 单元测试: 登录协议状态机(假 transport 全离线, 零真实账号)."""

import json
import os
from urllib.parse import parse_qsl

import pytest

from shuimu.core import campus
from shuimu.core.campus import (
    CHECK_URL,
    DOUBLE_AUTH_URL,
    GRJBXX_URL,
    LOGIN_ENTRY_URL,
    ROAM_FORM_URL,
    WEBVPN_LOGIN_URL,
    XSRF_URL,
    CampusClient,
    CampusProfile,
    ProtocolChangedError,
    SessionStore,
    extract_msg,
    extract_redirect_href,
    extract_sm2_key,
    find_fingerprint_form,
    perform_login,
    webvpn_wrap,
)
from shuimu.core.campus_net import CookieJar, MemoryTransport, TransportError
from shuimu.core.sm2 import G, _scalar_mul  # pylint: disable=protected-access

USER = "20231234567"
CALLBACK_URL = "https://oauth.tsinghua.edu.cn/thu-oauth/callback?sig=abc"
INFO_HREF = "https://info.tsinghua.edu.cn/f/info/gxfw_fg/common/index"
ID_FORM_URL = "https://id.tsinghua.edu.cn/do/off/ui/auth/login/form/oa"

# 测试用合法公钥(在曲线上), 登录页 fixture 注入
_POINT = _scalar_mul(0x6B7BF, G)
TEST_PUB = (_POINT[0].to_bytes(32, "big") + _POINT[1].to_bytes(32, "big")).hex()

LOGIN_PAGE = f'<div id="sm2publicKey">{TEST_PUB}</div>'
PROFILE_JSON = {"object": {"ryh": USER, "xm": "张三", "dwmc": "计算机系"}}


def success_page(href):
    """登录成功响应页(含跳转链接)."""
    return f'登录成功。正在重定向到 <a href="{href}">继续</a>'


def fingerprint_page(action="/do/off/ui/auth/login/checkSingle"):
    """信任设备自动提交表单页."""
    return (f'<form action="{action}">'
            '<input name="fingerPrint" value="">'
            '<input name="i_rememberme" value="on"></form>')


@pytest.fixture
def store(tmp_path):
    """隔离的会话存储."""
    return SessionStore(str(tmp_path / "campus-session.json"),
                        str(tmp_path / "campus-fingerprint"))


def add_login_prelude(transport):
    """公共前奏: webvpn 入口 302 -> id 登录页(带公钥)."""
    transport.add("GET", LOGIN_ENTRY_URL, status=302, location=ID_FORM_URL,
                  headers=[("Set-Cookie", "wengine_vpn=trip; Path=/")])
    transport.add("GET", ID_FORM_URL, text=LOGIN_PAGE)


def add_roam_info_tail(transport, info_page_text="portal"):
    """公共收尾: 漫游跳转 -> XSRF -> 本人信息."""
    transport.add("GET", webvpn_wrap(INFO_HREF), text=info_page_text)
    transport.add("GET", XSRF_URL, text="XSRF-TOKEN=tok123;")
    transport.add("GET", GRJBXX_URL + "?_csrf=tok123",
                  text=json.dumps(PROFILE_JSON))


def posts(transport, url):
    """按序取发往某 URL 的全部 POST (method, url, body, headers)."""
    return [r for r in transport.requests if r[0] == "POST" and r[1] == url]


def form_pairs(body):
    """POST body 解析回有序字段对."""
    return parse_qsl(body, keep_blank_values=True)


class TestHtmlParsers:
    """防御式 HTML 解析."""

    def test_sm2_key(self):
        """公钥提取; 缺失返回 None."""
        assert extract_sm2_key(LOGIN_PAGE) == TEST_PUB
        assert extract_sm2_key("<div id='other'>x</div>") is None

    def test_msg_note(self):
        """错误提示文本提取(含标签清理)."""
        page = '<span id="msg_note"> 用户名或<b>密码</b>错误 </span>'
        assert extract_msg(page) == "用户名或密码错误"

    def test_redirect_href(self):
        """首个链接提取."""
        assert extract_redirect_href(success_page(CALLBACK_URL)) == CALLBACK_URL

    def test_fingerprint_form(self):
        """信任设备表单的 action 与字段清单."""
        action, fields = find_fingerprint_form(fingerprint_page())
        assert action == "/do/off/ui/auth/login/checkSingle"
        assert fields == [("fingerPrint", ""), ("i_rememberme", "on")]

    def test_webvpn_wrap(self):
        """非 oauth 域名经 lbredirect 包装(query 不转义)."""
        wrapped = webvpn_wrap(INFO_HREF)
        assert wrapped.startswith(
            "https://oauth.tsinghua.edu.cn/lb-auth/lbredirect?")
        assert "host=info.tsinghua.edu.cn" in wrapped
        assert "uri=/f/info/gxfw_fg/common/index" in wrapped
        assert webvpn_wrap(CALLBACK_URL) == CALLBACK_URL


class TestSessionStore:
    """会话持久化."""

    def test_save_load_roundtrip_and_mode(self, store):
        """保存/读回一致且权限 0o600."""
        store.save({"version": 1, "username": USER, "cookies": {}})
        assert store.load()["username"] == USER
        assert os.stat(store._path).st_mode & 0o777 == 0o600  # pylint: disable=protected-access

    def test_load_missing_or_corrupt(self, store):
        """缺失/损坏返回 None."""
        assert store.load() is None
        with open(store._path, "w", encoding="utf-8") as f:
            f.write("{broken")
        assert store.load() is None

    def test_fingerprint_stable_and_survives_logout(self, store):
        """指纹生成后稳定; 登出删会话不删指纹."""
        first = store.fingerprint()
        assert len(first) == 32
        assert store.fingerprint() == first
        store.save({"username": USER})
        store.clear_session()
        assert store.load() is None
        assert store.fingerprint() == first


class TestLoginSuccess:
    """完整成功登录(两阶段均走凭据)."""

    def _transport(self):
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text=success_page(INFO_HREF))
        add_roam_info_tail(transport)
        return transport

    def test_success_writes_session(self, store):
        """成功: 会话落盘, 含 cookies 与本人信息."""
        transport = self._transport()
        outcome = perform_login(store, lambda: transport, USER, "pw-secret")
        assert outcome.state == "success"
        assert outcome.profile.name == "张三"
        session = store.load()
        assert session["username"] == USER
        assert session["cookies"]["webvpn.tsinghua.edu.cn"] == \
            {"wengine_vpn": "trip"}
        assert session["profile"]["raw"]["dwmc"] == "计算机系"

    def test_credential_form_fields_and_order(self, store):
        """check 表单字段名与顺序逐项符合协议; i_pass 带 04 前缀."""
        transport = self._transport()
        perform_login(store, lambda: transport, USER, "pw")
        checks = posts(transport, CHECK_URL)
        assert len(checks) == 2
        names = [name for name, _ in form_pairs(checks[0][2])]
        assert names == ["i_user", "i_pass", "fingerPrint",
                         "fingerGenPrint", "i_captcha", "singleLogin",
                         "deviceName"]
        values = dict(form_pairs(checks[0][2]))
        assert values["i_user"] == USER
        assert values["i_pass"].startswith("04")
        assert len(values["i_pass"]) > 64
        assert values["fingerPrint"] == store.fingerprint()
        assert values["singleLogin"] == "on"
        assert values["deviceName"] == "水木工作台"

    def test_cookie_header_injected(self, store):
        """沿途 set-cookie 在同 host 后续请求以 Cookie 头回传."""
        transport = self._transport()
        perform_login(store, lambda: transport, USER, "pw")
        xsrf_requests = [r for r in transport.requests if r[1] == XSRF_URL]
        assert xsrf_requests[0][3]["Cookie"] == "wengine_vpn=trip"


class TestLoginFailures:
    """失败分支."""

    def test_bad_credentials_message(self, store):
        """密码错误: msg_note 文本透出, 不落会话."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL,
                      text='<span id="msg_note">用户名或密码错误</span>')
        outcome = perform_login(store, lambda: transport, USER, "wrong")
        assert outcome.state == "bad_credentials"
        assert "用户名或密码错误" in outcome.message
        assert store.load() is None

    def test_redirected_back_to_login(self, store):
        """callback 链落回 webvpn 登录页: 判定失败."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, status=302,
                      location=WEBVPN_LOGIN_URL)
        transport.add("GET", WEBVPN_LOGIN_URL, text="login again")
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "bad_credentials"

    def test_non_numeric_username(self, store):
        """学号非纯数字直接拒绝(零请求)."""
        transport = MemoryTransport()
        outcome = perform_login(store, lambda: transport, "abc", "pw")
        assert outcome.state == "bad_credentials"
        assert not transport.requests

    def test_protocol_error_on_unrecognized_page(self, store):
        """入口页不可识别: protocol_error, 不崩."""
        transport = MemoryTransport()
        transport.add("GET", LOGIN_ENTRY_URL, text="<html>empty</html>")
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "protocol_error"
        assert "改版" in outcome.message

    def test_network_error(self, store):
        """网络故障: network_error 且不带堆栈细节."""

        def factory():
            class _Broken(MemoryTransport):
                def fetch(self, url, method="GET", body=None, headers=None):
                    raise TransportError("dns fail")

            return _Broken()

        outcome = perform_login(store, factory, USER, "pw")
        assert outcome.state == "network_error"
        assert "dns fail" in outcome.message


class TestTwoFactor:
    """二次认证全流程."""

    def _transport(self):
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL,
                      text="<div>登录需要二次认证</div>")
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success", "object": {"hasWeChatBool": False,
                                             "phone": "138****1234",
                                             "hasTotp": True}}))
        transport.add("POST", DOUBLE_AUTH_URL,
                      text=json.dumps({"result": "success"}))
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success",
             "object": {"redirectUrl": "/do/off/ui/auth/login/finish"}}))
        transport.add("GET",
                      "https://id.tsinghua.edu.cn/do/off/ui/auth/login/finish",
                      text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text=success_page(INFO_HREF))
        add_roam_info_tail(transport)
        return transport

    def test_need_2fa_without_callback(self, store):
        """无 ask_2fa 回调: 返回 need_2fa 与可选方式."""
        transport = self._transport()
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "need_2fa"
        assert ("sms", "短信 138****1234") in outcome.options.methods()
        assert ("totp", "动态口令(TOTP)") in outcome.options.methods()

    def test_full_2fa_flow_sms(self, store):
        """短信方式: 选择 -> 发码 -> VERITY_CODE -> 完成登录."""
        transport = self._transport()
        sent = []

        def ask_2fa(options, send_code):
            assert ("sms", "短信 138****1234") in options.methods()
            send_code("sms")
            sent.append("sms")
            return ("sms", "123456", False)

        outcome = perform_login(store, lambda: transport, USER, "pw",
                                ask_2fa=ask_2fa)
        assert outcome.state == "success"
        assert sent == ["sms"]
        double_posts = posts(transport, DOUBLE_AUTH_URL)
        actions = [dict(form_pairs(p[2]))["action"] for p in double_posts]
        assert actions == ["FIND_APPROACHES", "SEND_CODE", "VERITY_CODE"]
        verify_body = dict(form_pairs(double_posts[2][2]))
        assert verify_body["vericode"] == "123456"
        assert store.load()["username"] == USER

    def test_totp_uses_verify_totp_code(self, store):
        """totp 方式走 VERITY_TOTP_CODE 且不发码."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text="<div>登录需要二次认证</div>")
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success", "object": {"hasWeChatBool": False,
                                             "phone": "",
                                             "hasTotp": True}}))
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success",
             "object": {"redirectUrl": "/do/off/ui/auth/login/finish"}}))
        transport.add("GET",
                      "https://id.tsinghua.edu.cn/do/off/ui/auth/login/finish",
                      text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text=success_page(INFO_HREF))
        add_roam_info_tail(transport)

        outcome = perform_login(store, lambda: transport, USER, "pw",
                                ask_2fa=lambda options, send:
                                ("totp", "654321", False))
        assert outcome.state == "success"
        actions = [dict(form_pairs(p[2]))["action"]
                   for p in posts(transport, DOUBLE_AUTH_URL)]
        assert actions == ["FIND_APPROACHES", "VERITY_TOTP_CODE"]

    def test_cancelled_2fa(self, store):
        """取消二次认证: cancelled, 不落会话."""
        transport = self._transport()
        outcome = perform_login(store, lambda: transport, USER, "pw",
                                ask_2fa=lambda options, send: None)
        assert outcome.state == "cancelled"
        assert store.load() is None

    def test_2fa_again_at_roam_when_device_untrusted(self, store):
        """未信任设备: 漫游阶段再要一次二次认证, 两轮交互后成功.

        复刻 2026-11-10 真实登录实测: 阶段一 2FA 后未信任设备,
        漫游的凭据提交再次返回二次认证页.
        """
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text="<div>登录需要二次认证</div>")
        for redirect in ("/do/off/ui/auth/login/finish1",
                         "/do/off/ui/auth/login/finish2"):
            transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
                {"result": "success", "object": {
                    "hasWeChatBool": False, "phone": "138****1234",
                    "hasTotp": False}}))
            transport.add("POST", DOUBLE_AUTH_URL,
                          text=json.dumps({"result": "success"}))
            transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
                {"result": "success", "object": {"redirectUrl": redirect}}))
            transport.add(
                "GET", "https://id.tsinghua.edu.cn" + redirect,
                text=success_page(
                    CALLBACK_URL if "1" in redirect else INFO_HREF))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text="<div>登录需要二次认证</div>")
        add_roam_info_tail(transport)
        rounds = []

        def ask_2fa(options, send_code):
            rounds.append(options.phone_masked)
            send_code("sms")
            return ("sms", f"12345{len(rounds)}", False)

        outcome = perform_login(store, lambda: transport, USER, "pw",
                                ask_2fa=ask_2fa)
        assert outcome.state == "success"
        assert rounds == ["138****1234", "138****1234"]
        codes = [dict(form_pairs(p[2])).get("vericode")
                 for p in posts(transport, DOUBLE_AUTH_URL)
                 if dict(form_pairs(p[2]))["action"] == "VERITY_CODE"]
        assert codes == ["123451", "123452"]
        assert store.load()["username"] == USER

    def test_2fa_at_roam_without_callback_reports_need_2fa(self, store):
        """漫游需 2FA 而未注入交互: need_2fa 结果附可选方式."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        transport.add("POST", CHECK_URL, text="<div>登录需要二次认证</div>")
        transport.add("POST", DOUBLE_AUTH_URL, text=json.dumps(
            {"result": "success", "object": {"hasWeChatBool": False,
                                             "phone": "138****1234",
                                             "hasTotp": False}}))
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "need_2fa"
        assert "门户漫游" in outcome.message
        assert outcome.options.phone_masked == "138****1234"
        assert store.load() is None

    def test_double_auth_error_message(self, store):
        """FIND_APPROACHES 失败: 服务端 msg 透出."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text="需要二次认证")
        transport.add("POST", DOUBLE_AUTH_URL,
                      text=json.dumps({"result": "fail", "msg": "请求过于频繁"}))
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "failed"
        assert "请求过于频繁" in outcome.message


class TestTrustedDevice:
    """信任设备免密分支(漫游阶段)."""

    def test_passwordless_roam(self, store):
        """漫游入口给免密表单: 本地指纹提交成功, 不再发密码."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=fingerprint_page())
        transport.add("POST",
                      "https://id.tsinghua.edu.cn/do/off/ui/auth/login/checkSingle",
                      text=success_page(INFO_HREF))
        add_roam_info_tail(transport)
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "success"
        single = posts(
            transport,
            "https://id.tsinghua.edu.cn/do/off/ui/auth/login/checkSingle")
        assert dict(form_pairs(single[0][2]))["fingerPrint"] == \
            store.fingerprint()
        assert len(posts(transport, CHECK_URL)) == 1   # 仅阶段一用过密码

    def test_untrusted_falls_back_to_password(self, store):
        """指纹被拒(响应回到凭据页): 回退用密码完成漫游."""
        transport = MemoryTransport()
        add_login_prelude(transport)
        transport.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        transport.add("GET", CALLBACK_URL, text="webvpn ok")
        transport.add("GET", ROAM_FORM_URL, text=fingerprint_page())
        transport.add("POST",
                      "https://id.tsinghua.edu.cn/do/off/ui/auth/login/checkSingle",
                      text=LOGIN_PAGE)   # 回到凭据表单 = 未信任
        transport.add("POST", CHECK_URL, text=success_page(INFO_HREF))
        add_roam_info_tail(transport)
        outcome = perform_login(store, lambda: transport, USER, "pw")
        assert outcome.state == "success"
        assert len(posts(transport, CHECK_URL)) == 2


class TestCheckSession:
    """既有会话校验."""

    def _client_with_session(self, transport, store):
        """直接落一份会话文件并构造带 cookie 的客户端(不走网络)."""
        cookies = {"webvpn.tsinghua.edu.cn": {"c": "1"}}
        store.save({"version": 1, "username": USER, "cookies": cookies,
                    "profile": {"username": USER, "name": "张三", "raw": {}},
                    "checked_at": 0, "saved_at": 0})
        jar = CookieJar()
        jar.load(cookies)
        return CampusClient(transport, jar, store.fingerprint())

    def test_valid_session_returns_profile(self, store):
        """ryh 与学号一致: 返回本人信息并更新缓存时间."""
        transport = MemoryTransport()
        transport.add("GET", XSRF_URL, text="XSRF-TOKEN=tok;")
        transport.add("GET", GRJBXX_URL + "?_csrf=tok",
                      text=json.dumps(PROFILE_JSON))
        client = self._client_with_session(transport, store)
        profile = client.check_session(USER)
        assert profile is not None
        assert profile.name == "张三"
        assert ("单位", "计算机系") in profile.display_fields()

    def test_invalid_session_returns_none(self, store):
        """会话失效(302 回登录页): 返回 None."""
        transport = MemoryTransport()
        transport.add("GET", XSRF_URL, text="XSRF-TOKEN=tok;")
        transport.add("GET", GRJBXX_URL + "?_csrf=tok", status=302,
                      location=WEBVPN_LOGIN_URL)
        transport.add("GET", WEBVPN_LOGIN_URL, text="login")
        client = self._client_with_session(transport, store)
        assert client.check_session(USER) is None

    def test_mismatched_ryh_returns_none(self, store):
        """响应 ryh 与学号不符: 视为失效."""
        transport = MemoryTransport()
        transport.add("GET", XSRF_URL, text="XSRF-TOKEN=tok;")
        transport.add("GET", GRJBXX_URL + "?_csrf=tok", text=json.dumps(
            {"object": {"ryh": "20219999999", "xm": "他人"}}))
        client = self._client_with_session(transport, store)
        assert client.check_session(USER) is None


class TestProtocolGuard:
    """协议异常防护."""

    def test_protocol_changed_error_message(self):
        """ProtocolChangedError 是 CampusError 的子类."""
        with pytest.raises(campus.CampusError):
            raise ProtocolChangedError("页面结构不符")

    def test_profile_display_fields_defensive(self):
        """未知短字符串字段原样展示, 非字符串跳过."""
        profile = CampusProfile(USER, "张三",
                                {"ryh": USER, "xm": "张三", "note": "本科生",
                                 "count": 3, "long": "x" * 300})
        fields = dict(profile.display_fields())
        assert fields["note"] == "本科生"
        assert "count" not in fields and "long" not in fields
