"""右栏"校园"页签: 插件启用引导 / 登录表单 / 本人信息三态.

会话与协议全部经 controller.campus_* 动作; 构造与页签切换零联网(只读
会话文件). 登录为同步调用(每请求 15s 超时上限, 网络慢时界面短暂冻结,
本轮不引线程). 二次认证经 TwoFactorDialog(ask_2fa 回调内弹出).
"""

import time

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from shuimu.core.campus import CampusProfile
from shuimu.core.controller import Controller
from shuimu.gui import theme
from shuimu.gui.dialogs import confirm_network_enable, warn

PAGE_DISABLED, PAGE_LOGIN, PAGE_LOGGED = 0, 1, 2


class TwoFactorDialog(QDialog):
    """二次认证: 方式选择 + 发送验证码 + 验证码输入 + 信任此设备."""

    def __init__(self, options, send_code, parent=None):
        super().__init__(parent)
        self.setWindowTitle("二次认证")
        self._send_code = send_code
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.method_box = QComboBox()
        for value, label in options.methods():
            self.method_box.addItem(label, value)
        form.addRow("验证方式:", self.method_box)
        self.code_edit = QLineEdit()
        self.code_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.code_edit.setPlaceholderText("6 位验证码")
        form.addRow("验证码:", self.code_edit)
        self.trust_check = QCheckBox("信任此设备(免下次验证)")
        form.addRow("", self.trust_check)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.send_button = QPushButton("发送验证码")
        self.send_button.clicked.connect(self._send)
        row.addWidget(self.send_button)
        confirm_button = QPushButton("确定")
        confirm_button.clicked.connect(self._accept)
        row.addWidget(confirm_button)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        row.addWidget(cancel_button)
        layout.addLayout(row)

    def _send(self):
        """按当前方式发送验证码(totp 无需发送)."""
        if self.method_box.currentData() == "totp":
            self.send_button.setText("动态口令无需发送")
            self.send_button.setEnabled(False)
            return
        self._send_code(self.method_box.currentData())
        self.send_button.setText("已发送")
        self.send_button.setEnabled(False)

    def _accept(self):
        """验证码非空才接受."""
        if not self.code_edit.text().strip():
            self.code_edit.setFocus()
            return
        self.accept()

    def answer(self) -> tuple:
        """(方式, 验证码, 信任此设备)."""
        return (self.method_box.currentData(),
                self.code_edit.text().strip(),
                self.trust_check.isChecked())


class CampusTab(QWidget):
    """右栏校园页签: 会话状态 + 登录表单 + 本人信息."""

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self._ctl = ctl
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self._build_disabled_page()
        self._build_login_page()
        self._build_logged_page()
        self.refresh_state()

    # ---- 三页构建 ----

    def _build_disabled_page(self) -> None:
        """页 0: 未启用(说明 + 启用按钮)."""
        page = QWidget()
        column = QVBoxLayout(page)
        hint = QLabel("校园服务可登录清华统一认证(WebVPN), 获取本人信息, "
                      "并为将来的学堂/云盘插件提供会话.\n"
                      "该插件需要访问网络, 默认禁用.")
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.enable_button = QPushButton("启用校园插件")
        self.enable_button.clicked.connect(self._enable_plugin)
        column.addWidget(self.enable_button)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_login_page(self) -> None:
        """页 1: 登录表单."""
        page = QWidget()
        column = QVBoxLayout(page)
        form = QFormLayout()
        self.username_edit = QLineEdit()
        form.addRow("学号:", self.username_edit)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("密码:", self.password_edit)
        column.addLayout(form)
        row = QHBoxLayout()
        self.login_button = QPushButton("登录")
        self.login_button.clicked.connect(self._login)
        row.addWidget(self.login_button)
        row.addStretch(1)
        column.addLayout(row)
        note = QLabel("密码仅用于本次登录, 不会保存; 登录态保存在本机 data/ 下.")
        note.setWordWrap(True)
        column.addWidget(note)
        self.status_label = QLabel("未登录")
        column.addWidget(self.status_label)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_logged_page(self) -> None:
        """页 2: 本人信息 + 刷新/重新登录/登出."""
        page = QWidget()
        column = QVBoxLayout(page)
        self.logged_label = QLabel("")
        self.logged_label.setWordWrap(True)
        column.addWidget(self.logged_label)
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        column.addWidget(self.profile_label)
        self.notice_label = QLabel("")
        self.notice_label.setStyleSheet(f"color: {theme.DANGER};")
        self.notice_label.hide()
        column.addWidget(self.notice_label)
        row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新状态")
        self.refresh_button.clicked.connect(self._refresh_session)
        row.addWidget(self.refresh_button)
        self.relogin_button = QPushButton("重新登录")
        self.relogin_button.clicked.connect(
            lambda: self.stack.setCurrentIndex(PAGE_LOGIN))
        self.relogin_button.hide()
        row.addWidget(self.relogin_button)
        self.logout_button = QPushButton("登出")
        self.logout_button.clicked.connect(self._logout)
        row.addWidget(self.logout_button)
        row.addStretch(1)
        column.addLayout(row)
        column.addStretch(1)
        self.stack.addWidget(page)

    # ---- 状态机 ----

    def refresh_state(self) -> None:
        """按插件与会话状态切换页面(零联网)."""
        record = self._ctl.plugins.get("campus")
        if record is None or record.status != "ok":
            self.stack.setCurrentIndex(PAGE_DISABLED)
            return
        result = self._ctl.campus_status()
        if result.detail.get("state") == "logged_out":
            self.stack.setCurrentIndex(PAGE_LOGIN)
            return
        self._show_session(result)
        self.stack.setCurrentIndex(PAGE_LOGGED)

    def _show_session(self, result) -> None:
        """已登录页内容(来自会话缓存)."""
        session = result.detail.get("session") or {}
        username = str(session.get("username", ""))
        cached = session.get("profile") or {}
        name = str(cached.get("name", ""))
        checked_at = session.get("checked_at")
        checked_text = time.strftime("%m-%d %H:%M",
                                     time.localtime(checked_at)) \
            if checked_at else "未校验"
        self.logged_label.setText(f"已登录: {username} {name}".rstrip())
        profile = CampusProfile(username, name, dict(cached.get("raw") or {}))
        lines = [f"{label}: {value}"
                 for label, value in profile.display_fields()]
        lines.append(f"(上次校验: {checked_text})")
        self.profile_label.setText("\n".join(lines))
        self.notice_label.hide()
        self.relogin_button.hide()

    # ---- 动作 ----

    def _enable_plugin(self) -> None:
        """启用校园插件(经网络权限确认框)."""
        result = self._ctl.set_plugin_enabled(
            "campus", True,
            confirm=lambda manifest: confirm_network_enable(self, manifest))
        if not result.ok:
            warn(self, "未能启用校园插件", result.message)
        self.refresh_state()

    def _login(self) -> None:
        """同步登录; 需要时弹二次认证对话框."""
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self.status_label.setText("请输入学号与密码")
            return
        self.login_button.setEnabled(False)
        self.status_label.setText("登录中...(可能需要数秒)")
        self.repaint()
        result = self._ctl.campus_login(username, password,
                                        ask_2fa=self._ask_2fa)
        self.login_button.setEnabled(True)
        self.password_edit.clear()
        self.status_label.setText(result.message)
        if result.ok:
            self.refresh_state()

    def _ask_2fa(self, options, send_code):
        """弹二次认证对话框; 取消返回 None(登录结果 cancelled)."""
        dialog = TwoFactorDialog(options, send_code, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.answer()

    def _refresh_session(self) -> None:
        """联网校验会话(网络异常与失效区分提示)."""
        self.refresh_button.setEnabled(False)
        self.repaint()
        result = self._ctl.campus_status(check=True)
        self.refresh_button.setEnabled(True)
        if result.ok:
            self._show_session(self._ctl.campus_status())
            return
        if result.detail.get("state") == "expired":
            self.notice_label.setText("会话已失效, 请重新登录")
            self.relogin_button.show()
        else:
            self.notice_label.setText(result.message)
        self.notice_label.show()

    def _logout(self) -> None:
        """登出并回到登录页."""
        self._ctl.campus_logout()
        self.refresh_state()
