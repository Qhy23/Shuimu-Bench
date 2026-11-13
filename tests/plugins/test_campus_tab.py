"""campus_tab 测试: 三态页签与二次认证对话框(offscreen, 假 controller)."""

from types import SimpleNamespace

from PySide6.QtWidgets import QLineEdit

from shuimu.core.campus import TwoFactorOptions
from shuimu.core.controller import ActionResult
from shuimu.plugins.campus.campus_tab import (
    PAGE_DISABLED,
    PAGE_LOGGED,
    PAGE_LOGIN,
    CampusTab,
    TwoFactorDialog,
)

PROFILE_RAW = {"ryh": "20231234567", "xm": "张三", "dwjc": "计算机系"}


def make_record(status="ok"):
    """campus 插件记录替身."""
    return SimpleNamespace(status=status, error="",
                           manifest=SimpleNamespace(name="校园账户登录"))


class FakeController:
    """CampusTab 依赖的 controller 替身(记录调用, 不碰网络)."""

    def __init__(self, status="ok", logged_in=False, require_2fa=False):
        self.record = make_record(status)
        self.logged_in = logged_in
        self.require_2fa = require_2fa
        self.login_calls = []
        self.enabled_calls = []
        self.logout_calls = 0
        self.plugins = SimpleNamespace(get=lambda pid: self.record)

    def campus_status(self, check=False):
        """会话状态替身."""
        if not self.logged_in:
            return ActionResult(False, "未登录", {"state": "logged_out"})
        session = {"username": "20231234567",
                   "profile": {"username": "20231234567", "name": "张三",
                               "raw": PROFILE_RAW},
                   "checked_at": 1762600000.0}
        if check:
            return ActionResult(True, "会话有效: 张三",
                                {"state": "valid"})
        return ActionResult(True, "已登录 20231234567 张三",
                            {"state": "cached", "session": session})

    def campus_login(self, username, password, ask_2fa=None):
        """登录替身: 记录入参; require_2fa 时走一遍回调."""
        self.login_calls.append((username, password))
        if self.require_2fa and ask_2fa is not None:
            answer = ask_2fa(TwoFactorOptions(phone_masked="138****1234",
                                              has_totp=True),
                             lambda _method: None)
            if answer is None:
                return ActionResult(False, "已取消二次认证",
                                    {"state": "cancelled"})
            method, code, _trust = answer
            self.logged_in = True
            return ActionResult(True, f"已登录: 张三(2FA {method}/{code})",
                                {"state": "success"})
        self.logged_in = True
        return ActionResult(True, "已登录: 张三", {"state": "success"})

    def campus_logout(self):
        """登出替身."""
        self.logout_calls += 1
        self.logged_in = False
        return ActionResult(True, "已登出, 会话已清除", {})

    def set_plugin_enabled(self, plugin_id, enabled, confirm=None):
        """启用替身(与真 controller 一致: network 启用先过 confirm)."""
        self.enabled_calls.append((plugin_id, enabled, confirm))
        manifest = self.record.manifest
        if enabled and confirm is not None and not confirm(manifest):
            return ActionResult(False, "已取消: 网络权限未确认", {})
        self.record = make_record("ok" if enabled else "disabled")
        return ActionResult(True, "已启用插件 校园账户登录", {})


def make_tab(qtbot, ctl):
    """构造页签并挂到 qtbot."""
    tab = CampusTab(ctl)
    qtbot.addWidget(tab)
    return tab


class TestCampusTabStates:
    """三态切换."""

    def test_disabled_when_plugin_not_enabled(self, qtbot):
        """插件未启用: 显示启用引导页."""
        tab = make_tab(qtbot, FakeController(status="disabled"))
        assert tab.stack.currentIndex() == PAGE_DISABLED

    def test_login_page_when_not_logged_in(self, qtbot):
        """已启用未登录: 显示登录表单."""
        tab = make_tab(qtbot, FakeController(status="ok", logged_in=False))
        assert tab.stack.currentIndex() == PAGE_LOGIN

    def test_logged_page_shows_profile(self, qtbot):
        """已登录: 学号姓名与本人信息字段."""
        tab = make_tab(qtbot, FakeController(status="ok", logged_in=True))
        assert tab.stack.currentIndex() == PAGE_LOGGED
        assert "20231234567" in tab.logged_label.text()
        text = tab.profile_label.text()
        assert "姓名: 张三" in text
        assert "院系: 计算机系" in text
        assert "上次校验" in text

    def test_enable_button_flow(self, qtbot, monkeypatch):
        """启用按钮 -> 确认回调 -> 切到登录页."""
        confirmed = []
        monkeypatch.setattr("shuimu.plugins.campus.campus_tab.confirm_network_enable",
                            lambda parent, manifest:
                            confirmed.append(manifest.name) or True)
        ctl = FakeController(status="disabled")
        tab = make_tab(qtbot, ctl)
        tab.enable_button.click()
        assert confirmed == ["校园账户登录"]
        assert ctl.enabled_calls and ctl.enabled_calls[0][0] == "campus"
        assert ctl.enabled_calls[0][2] is not None   # 带确认回调
        assert tab.stack.currentIndex() == PAGE_LOGIN

    def test_login_success_flow(self, qtbot):
        """登录成功: 密码清空, 状态行显示消息, 切到已登录页."""
        ctl = FakeController(status="ok", logged_in=False)
        tab = make_tab(qtbot, ctl)
        tab.username_edit.setText("20231234567")
        tab.password_edit.setText("pw-secret")
        tab.login_button.click()
        assert ctl.login_calls == [("20231234567", "pw-secret")]
        assert tab.password_edit.text() == ""
        assert "已登录" in tab.status_label.text()
        assert tab.stack.currentIndex() == PAGE_LOGGED

    def test_login_missing_input_hint(self, qtbot):
        """缺学号或密码: 状态行提示且不调用登录."""
        ctl = FakeController(status="ok", logged_in=False)
        tab = make_tab(qtbot, ctl)
        tab.login_button.click()
        assert not ctl.login_calls
        assert "请输入" in tab.status_label.text()

    def test_login_with_2fa_dialog(self, qtbot, monkeypatch):
        """需要二次认证: 弹窗返回答案后登录成功."""
        ctl = FakeController(status="ok", logged_in=False, require_2fa=True)
        tab = make_tab(qtbot, ctl)
        captured = {}

        def fake_exec(self):
            captured["dialog"] = self
            self.method_box.setCurrentIndex(0)   # sms
            self.code_edit.setText("123456")
            self.trust_check.setChecked(True)
            return TwoFactorDialog.DialogCode.Accepted

        monkeypatch.setattr(TwoFactorDialog, "exec", fake_exec)
        tab.username_edit.setText("20231234567")
        tab.password_edit.setText("pw-secret")
        tab.login_button.click()
        assert captured["dialog"].answer()[:2] == ("sms", "123456")
        assert "2FA sms/123456" in tab.status_label.text()

    def test_refresh_expired_shows_relogin(self, qtbot):
        """会话失效: 警示行与重新登录按钮出现."""

        class ExpiredController(FakeController):
            """联网校验恒失效的替身."""

            def campus_status(self, check=False):
                if check:
                    return ActionResult(False, "会话已失效, 请重新登录",
                                        {"state": "expired"})
                return super().campus_status(check=False)

        tab = make_tab(qtbot, ExpiredController(status="ok", logged_in=True))
        tab.refresh_button.click()
        assert tab.notice_label.isVisibleTo(tab)
        assert tab.relogin_button.isVisibleTo(tab)
        tab.relogin_button.click()
        assert tab.stack.currentIndex() == PAGE_LOGIN

    def test_logout_returns_to_login(self, qtbot):
        """登出后回到登录页."""
        ctl = FakeController(status="ok", logged_in=True)
        tab = make_tab(qtbot, ctl)
        tab.logout_button.click()
        assert ctl.logout_calls == 1
        assert tab.stack.currentIndex() == PAGE_LOGIN


class TestTwoFactorDialog:
    """二次认证对话框."""

    def _options(self):
        return TwoFactorOptions(has_wechat=True, phone_masked="138****1234",
                                has_totp=True)

    def test_methods_populated(self, qtbot):
        """方式下拉含短信/微信/TOTP."""
        dialog = TwoFactorDialog(self._options(), lambda _m: None)
        qtbot.addWidget(dialog)
        labels = [dialog.method_box.itemText(i)
                  for i in range(dialog.method_box.count())]
        assert labels == ["短信 138****1234", "微信", "动态口令(TOTP)"]

    def test_send_code_button(self, qtbot):
        """发送按钮调闭包并自禁用."""
        sent = []
        dialog = TwoFactorDialog(self._options(), sent.append)
        qtbot.addWidget(dialog)
        dialog._send()
        assert sent == ["sms"]
        assert not dialog.send_button.isEnabled()

    def test_totp_no_send(self, qtbot):
        """totp 方式无需发送."""
        sent = []
        dialog = TwoFactorDialog(self._options(), sent.append)
        qtbot.addWidget(dialog)
        dialog.method_box.setCurrentIndex(2)
        dialog._send()
        assert not sent
        assert not dialog.send_button.isEnabled()

    def test_empty_code_not_accepted(self, qtbot):
        """空验证码不关闭对话框."""
        dialog = TwoFactorDialog(self._options(), lambda _m: None)
        qtbot.addWidget(dialog)
        dialog._accept()
        assert not dialog.result()

    def test_answer(self, qtbot):
        """答案三元组."""
        dialog = TwoFactorDialog(self._options(), lambda _m: None)
        qtbot.addWidget(dialog)
        dialog.code_edit.setText("654321")
        dialog.trust_check.setChecked(True)
        assert dialog.answer() == ("sms", "654321", True)
        # 验证码框为掩码输入
        assert dialog.code_edit.echoMode() == QLineEdit.EchoMode.Password
