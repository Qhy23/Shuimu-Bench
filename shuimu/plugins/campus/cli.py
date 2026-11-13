"""校园账户插件的 CLI 增强: campus-* 子命令的注册与执行.

campus 专属逻辑跟插件包走, cli/main.py 只做两行接线(注册与分发).
"""

import getpass
import sys

from shuimu.core.actions import ActionResult
from shuimu.core.controller import Controller

CAMPUS_COMMANDS = ("campus-login", "campus-status", "campus-logout")


def build_campus_subcommands(sub) -> None:
    """注册校园账户相关子命令(controller 校园动作的薄封装)."""
    p_login = sub.add_parser(
        "campus-login", help="登录校园统一认证(密码交互输入, 不落盘)")
    p_login.add_argument("username", nargs="?", help="学号(缺省交互输入)")
    p_login.add_argument("--method",
                         help="二次认证方式 sms/wechat/totp(非交互)")
    p_login.add_argument("--code", help="二次认证验证码(非交互)")
    p_login.add_argument("--trust", action="store_true",
                         help="信任此设备(漫游免二次认证)")

    p_status = sub.add_parser(
        "campus-status", help="校园会话状态与本人信息(默认联网校验)")
    p_status.add_argument("--no-check", action="store_true",
                          help="只读本机会话, 不联网")

    sub.add_parser("campus-logout", help="清除本机会话(设备指纹保留)")


def run_campus_command(ctl: Controller, args) -> int:
    """执行校园相关子命令; 供 cli/main.py 的分发调用."""
    if args.command == "campus-login":
        return _cmd_campus_login(ctl, args)
    if args.command == "campus-status":
        return _cmd_campus_status(ctl, args)
    return _emit(ctl.campus_logout())


def _emit(result: ActionResult) -> int:
    """打印 ActionResult 并按成败给退出码(与 cli.main._emit 同契约)."""
    print(result.message, file=sys.stderr if not result.ok else sys.stdout)
    return 0 if result.ok else 2


def _campus_ask_2fa(options, send_code):
    """CLI 二次认证交互: 列方式 -> 选择 -> (发码) -> 输码 -> 问信任."""
    methods = options.methods()
    if not methods:
        print("服务端未提供可用的二次认证方式", file=sys.stderr)
        return None
    for i, (_value, label) in enumerate(methods, 1):
        print(f"  {i}. {label}")
    pick = input("选择验证方式(序号, 直接回车取消): ").strip()
    if not pick.isdigit() or not 1 <= int(pick) <= len(methods):
        return None
    method = methods[int(pick) - 1][0]
    if method != "totp":
        send_code(method)
        print("  验证码已发送")
    code = getpass.getpass("验证码(输入不回显): ").strip()
    trust = input("信任此设备? (y/N): ").strip().lower() == "y"
    return method, code, trust


def _print_profile(result) -> None:
    """本人信息逐行打印(有则打印)."""
    profile = result.detail.get("profile")
    if profile is not None:
        for label, value in profile.display_fields():
            print(f"  {label}: {value}")


def _cmd_campus_login(ctl: Controller, args) -> int:
    """campus-login: 学号/密码交互输入(密码绝不走 argv)."""
    username = args.username or input("学号: ").strip()
    password = getpass.getpass("密码(输入不回显): ")
    if args.method and args.code:
        answer = (args.method, args.code, args.trust)

        def preset_2fa(_options, _send):
            """非交互: 直接用预置答案."""
            return answer

        ask_2fa = preset_2fa
    else:
        ask_2fa = _campus_ask_2fa
    result = ctl.campus_login(username, password, ask_2fa=ask_2fa)
    exit_code = _emit(result)
    _print_profile(result)
    options = result.detail.get("options")
    if result.detail.get("state") == "need_2fa" and options is not None:
        labels = " / ".join(label for _, label in options.methods())
        print(f"可选方式: {labels}")
        print("带 --method 与 --code 重跑, 或在 GUI 校园页签交互完成")
    return exit_code


def _cmd_campus_status(ctl: Controller, args) -> int:
    """campus-status: 默认联网校验, --no-check 只读本机会话."""
    result = ctl.campus_status(check=not args.no_check)
    exit_code = _emit(result)
    _print_profile(result)
    return exit_code
