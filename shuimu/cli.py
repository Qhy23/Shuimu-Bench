"""命令行接口: 与 GUI 共用存储与整理逻辑, 供无头环境与 AI 工具外部测试.

两种用法:
  一次性: python cli.py <子命令> [参数]
  交互式: python cli.py            (不带子命令进入 REPL, 逐行输入命令)

示例:
  python cli.py add-course 高等数学
  python cli.py add 1 /path/to/file.pdf
  python cli.py add 1 https://learn.tsinghua.edu.cn
  python cli.py resources 1
  python cli.py organize ./downloads --yes
  python cli.py log
"""

import argparse
import os
import shlex
import sys

from .app import default_db_path
from .organize import build_plan, execute_plan
from .storage import Storage

KIND_LABEL = {"file": "文件", "dir": "目录", "link": "链接"}


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器."""
    parser = argparse.ArgumentParser(prog="shuimu", description=__doc__)
    parser.add_argument("--db", default=default_db_path(),
                        help="数据库路径(默认 data/shuimu.db; 仅启动时生效)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("courses", help="列出全部课程")

    p_add_course = sub.add_parser("add-course", help="新建课程")
    p_add_course.add_argument("name")

    p_add = sub.add_parser("add", help="把文件/目录/链接登记进课程")
    p_add.add_argument("course_id", type=int)
    p_add.add_argument("target", help="本地路径或 http(s) 链接")

    p_ls = sub.add_parser("resources", help="列出课程资源及状态")
    p_ls.add_argument("course_id", type=int, nargs="?",
                      help="缺省列出全部课程的资源")

    p_rm = sub.add_parser("remove", help="按 id 从索引移除资源(不动磁盘)")
    p_rm.add_argument("resource_id", type=int)

    p_org = sub.add_parser("organize", help="整理目录: 默认只预览, --yes 才执行")
    p_org.add_argument("directory")
    p_org.add_argument("--yes", action="store_true", help="确认执行移动")

    p_log = sub.add_parser("log", help="查看操作留痕")
    p_log.add_argument("--limit", type=int, default=20)
    return parser


def _detect_kind(target: str) -> str:
    """按形态判断资源类型: http(s) 为链接, 目录为 dir, 其余为 file."""
    if target.startswith(("http://", "https://")):
        return "link"
    return "dir" if os.path.isdir(target) else "file"


def _print_resources(storage: Storage, course_id=None) -> None:
    """打印资源列表; course_id 为 None 时遍历全部课程."""
    courses = storage.list_courses()
    ids = [course_id] if course_id is not None else [c["id"] for c in courses]
    names = {c["id"]: c["name"] for c in courses}
    for cid in ids:
        print(f"== 课程 {cid} {names.get(cid, '(未知)')} ==")
        for res in storage.list_resources(cid):
            exists, changed = Storage.check_resource(res)
            status = "失效" if not exists else ("已变更" if changed else "ok")
            print(f"  #{res['id']} [{KIND_LABEL[res['kind']]}] {res['display_name']}"
                  f" | {status} | {res['path_or_url']}")


def run_command(storage: Storage, args) -> int:
    """执行一条已解析的命令, 返回退出码. 不持有连接, 由调用方管理."""
    if args.command == "courses":
        for c in storage.list_courses():
            print(f"#{c['id']} {c['name']}")

    elif args.command == "add-course":
        cid = storage.add_course(args.name)
        print(f"已建课程 #{cid} {args.name}")

    elif args.command == "add":
        kind = _detect_kind(args.target)
        if kind != "link" and not os.path.exists(args.target):
            print(f"路径不存在: {args.target}", file=sys.stderr)
            return 2
        rid = storage.add_resource(args.course_id, kind, args.target)
        storage.log_operation("add_resource", rid, path_snapshot=args.target)
        print(f"已登记 #{rid} [{KIND_LABEL[kind]}] {args.target}")

    elif args.command == "resources":
        _print_resources(storage, args.course_id)

    elif args.command == "remove":
        res = storage.get_resource(args.resource_id)
        if res is None:
            print(f"资源不存在: #{args.resource_id}", file=sys.stderr)
            return 2
        storage.remove_resource(args.resource_id)
        storage.log_operation("remove_resource",
                              path_snapshot=res["path_or_url"])
        print(f"已移除 #{args.resource_id}")

    elif args.command == "organize":
        return _cmd_organize(storage, args)

    elif args.command == "log":
        for op in storage.list_operations(args.limit):
            print(f"#{op['id']} [{op['status']}] {op['action']}"
                  f" | {op['path_snapshot'] or '-'} | {op['detail'] or ''}")
    return 0


def main(argv=None) -> int:
    """CLI 入口: 带子命令则单次执行, 不带则进入交互模式."""
    args = build_parser().parse_args(argv)
    storage = Storage(args.db)
    try:
        if args.command is None:
            return repl(storage)
        return run_command(storage, args)
    finally:
        storage.close()


def repl(storage: Storage) -> int:
    """交互模式: 一次启动, 逐行输入子命令; help 查看用法, exit/quit 退出."""
    print("水木工作台 CLI 交互模式. 输入 help 查看命令, exit 退出.")
    parser = build_parser()
    while True:
        try:
            line = input("shuimu> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("exit", "quit"):
            return 0
        if line == "help":
            parser.print_help()
            continue
        try:
            args = parser.parse_args(shlex.split(line))
        except SystemExit:
            continue  # argparse 解析失败时已打印错误, 不退出 REPL
        if getattr(args, "command", None):
            run_command(storage, args)
        else:
            parser.print_help()


def _cmd_organize(storage: Storage, args) -> int:
    """整理子命令: 先预览, --yes 才执行并留痕."""
    try:
        plan = build_plan(args.directory)
    except NotADirectoryError:
        print(f"目录不存在: {args.directory}", file=sys.stderr)
        return 2
    for item in plan:
        print(f"  {item.src} -> {item.dst}")
    if not args.yes:
        print(f"共 {len(plan)} 项(预览). 加 --yes 执行.")
        return 0
    moved, errors = execute_plan(plan)
    status = "ok" if not errors else "failed"
    storage.log_operation("organize", status=status,
                          params={"directory": args.directory},
                          path_snapshot=args.directory,
                          detail={"moved": moved, "errors": errors})
    print(f"已移动 {moved} 个, 失败 {len(errors)} 个.")
    for err in errors:
        print(f"  失败: {err}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
