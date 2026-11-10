"""校园账户登录插件入口: 复用 core/campus 协议层, 按 action 分派.

主通道是 controller.campus_* 动作(GUI 校园页签与 CLI 专用子命令, 交互
体验更好: 密码 getpass/掩码输入, 2FA 对话框); 本入口保持与通用插件框架
的对接(plugin-run campus). 密码与验证码经 secret 参数传入, 宿主留痕时
自动剔除其值; 本模块自身也不将其写入任何 report 字段.
"""

from shuimu.core.campus import SessionStore, perform_login
from shuimu.core.campus_net import UrllibTransport
from shuimu.core.paths import (
    default_campus_fingerprint_path,
    default_campus_session_path,
)
from shuimu.core.pluginapi import (
    PluginJob,
    PluginReport,
    finding,
    make_report,
)


def _store() -> SessionStore:
    """会话存储(测试可替换)."""
    return SessionStore(default_campus_session_path(),
                        default_campus_fingerprint_path())


def _transport_factory() -> UrllibTransport:
    """生产传输层(测试可替换为 MemoryTransport)."""
    return UrllibTransport()


def run(job: PluginJob) -> PluginReport:
    """按 params.action 分派: status(只读) / login / logout."""
    action = job.params.get("action", "status")
    if action == "login":
        return _login(job.params)
    if action == "logout":
        return _logout()
    return _status()


def _status() -> PluginReport:
    """只读会话状态(不联网)."""
    session = _store().load()
    if session is None:
        return make_report([finding("ok", "session", "未登录")],
                           summary="未登录")
    username = str(session.get("username", ""))
    name = str((session.get("profile") or {}).get("name", ""))
    findings = [finding("ok", "session", f"已登录 {username} {name}".strip(),
                        "会话保存在本机; 联网校验用 GUI 校园页签或 campus-status")]
    return make_report(findings, summary=f"已登录 {username} {name}".strip())


def _preset_two_factor(answer: tuple):
    """构造非交互的 ask_2fa 回调(直接返回预置答案)."""
    def ask_2fa(_options, _send):
        return answer
    return ask_2fa


def _login(params: dict) -> PluginReport:
    """登录; 预置 method+code 时非交互完成二次认证(totp 直用)."""
    username = str(params.get("username", "")).strip()
    password = str(params.get("password", ""))
    if not username or not password:
        return make_report(
            [finding("error", "input", "缺 username 或 password 参数")],
            summary="参数不全")
    method = str(params.get("method", "")).strip()
    code = str(params.get("code", "")).strip()
    ask_2fa = None
    if method and code:
        ask_2fa = _preset_two_factor(
            (method, code, bool(params.get("trust"))))
    outcome = perform_login(_store(), _transport_factory, username, password,
                            ask_2fa=ask_2fa)
    if outcome.state == "need_2fa":
        methods = outcome.options.methods() if outcome.options else []
        labels = " / ".join(label for _, label in methods) or "无可用方式"
        return make_report([
            finding("error", "two_factor", "需要二次认证", labels),
            finding("warn", "two_factor",
                    "带 method 与 code 参数重新运行可完成登录",
                    "方式值: " + " / ".join(value for value, _ in methods)),
        ], summary="需要二次认证, 未完成登录")
    if outcome.state != "success":
        return make_report([finding("error", "login", outcome.message)],
                           summary=f"登录失败: {outcome.message}")
    findings = [finding("ok", "login", outcome.message)]
    findings.extend(finding("ok", "profile", f"{label}: {value}")
                    for label, value in outcome.profile.display_fields())
    return make_report(findings, summary=outcome.message)


def _logout() -> PluginReport:
    """清除本机会话(设备指纹保留)."""
    store = _store()
    existed = store.load() is not None
    store.clear_session()
    summary = "已登出, 会话已清除" if existed else "本无会话"
    return make_report([finding("ok", "logout", summary)], summary=summary)
