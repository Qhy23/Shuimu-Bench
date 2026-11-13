"""清华云盘插件的 CLI 增强: cloud-* 子命令的注册与执行.

云盘专属逻辑跟插件包走, cli/main.py 只做两行接线(注册与分发).
"""

import sys

from shuimu.core.actions import ActionResult
from shuimu.core.controller import Controller

CLOUD_COMMANDS = ("cloud-browse", "cloud-mount")


def build_cloud_subcommands(sub) -> None:
    """注册清华云盘相关子命令(controller 云盘动作的薄封装)."""
    p_browse = sub.add_parser(
        "cloud-browse",
        help="浏览云盘分享链接: 列目录并预览课程匹配(只读, 不登记)")
    p_browse.add_argument("url", help="分享链接(.../d/<分享id>/)")
    p_browse.add_argument("--depth", type=int, default=2,
                          help="目录匹配递归层数(1-3, 默认 2)")

    p_mount = sub.add_parser(
        "cloud-mount",
        help="挂载云盘分享库: 匹配目录登记为课程链接资源")
    p_mount.add_argument("url", help="分享链接(.../d/<分享id>/)")
    p_mount.add_argument("--depth", type=int, default=2,
                         help="目录匹配递归层数(1-3, 默认 2)")


def run_cloud_command(ctl: Controller, args) -> int:
    """执行云盘相关子命令; 供 cli/main.py 的分发调用."""
    if args.command == "cloud-browse":
        result = ctl.cloud_browse(args.url, args.depth)
        code = _emit(result)
        if result.ok:
            _print_plans(result)
        return code
    result = ctl.cloud_mount(args.url, args.depth)
    return _emit(result)


def _emit(result: ActionResult) -> int:
    """打印 ActionResult 并按成败给退出码(与 cli.main._emit 同契约)."""
    print(result.message, file=sys.stderr if not result.ok else sys.stdout)
    return 0 if result.ok else 2


def _print_plans(result: ActionResult) -> None:
    """cloud-browse 的匹配明细(目录 -> 课程)."""
    for plan in result.detail.get("mounts") or []:
        print(f"{plan['path']}\n    -> {plan['course_name']}\n"
              f"    {plan['url']}")
    unmatched = result.detail.get("unmatched") or []
    for name in unmatched:
        print(f"{name}\n    -> (未匹配)")
    if not result.detail.get("mounts") and not unmatched:
        print("(未发现子目录)")
