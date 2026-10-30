"""命令行接口: 与 GUI 共用存储与业务逻辑(Controller), 供无头环境与 AI 工具外部测试.

两种用法:
  一次性: python cli.py <子命令> [参数]
  交互式: python cli.py            (不带子命令进入 REPL, 逐行输入命令)

示例:
  python cli.py add-course 高等数学
  python cli.py add 1 /path/to/file.pdf
  python cli.py resources 1
  python cli.py search 关键词
  python cli.py organize ./downloads --yes
  python cli.py schedule --week 6
  python cli.py add-todo 微积分作业 --due 2026-10-26
  python cli.py import-schedule 个人校历.html
  python cli.py add-event 答疑 --weekday 3 --session 1 --weeks 1-8
"""

import argparse
import shlex
import sys
from datetime import date, time, timedelta

from shuimu.core.controller import KIND_LABEL, Controller
from shuimu.core.paths import default_db_path, default_schedule_path
from shuimu.core.schedule import (
    SESSION_TIMES,
    WEEKDAY_NAMES,
    Event,
    entries_for_week,
    expand_weekly,
    parse_week_list,
    sorted_todos,
    todo_status,
    week_label,
    week_start,
)
from shuimu.core.storage import Storage


def build_parser() -> argparse.ArgumentParser:
    """构建参数解析器."""
    parser = argparse.ArgumentParser(prog="shuimu", description=__doc__)
    parser.add_argument("--db", default=default_db_path(),
                        help="数据库路径(默认 data/shuimu.db; 仅启动时生效)")
    parser.add_argument("--schedule", default=default_schedule_path(),
                        help="日程 YAML 路径(默认 data/schedule.yaml)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("courses", help="列出全部课程")

    p_add_course = sub.add_parser("add-course", help="新建课程")
    p_add_course.add_argument("name")

    p_del_course = sub.add_parser("del-course", help="删除课程及其资源索引(不动磁盘)")
    p_del_course.add_argument("course_id", type=int)

    p_add = sub.add_parser("add", help="把文件/目录/链接登记进课程")
    p_add.add_argument("course_id", type=int)
    p_add.add_argument("target", help="本地路径或 http(s) 链接")

    p_ls = sub.add_parser("resources", help="列出课程资源及状态")
    p_ls.add_argument("course_id", type=int, nargs="?",
                      help="缺省列出全部课程的资源")

    p_rm = sub.add_parser("remove", help="按 id 从索引移除资源(不动磁盘)")
    p_rm.add_argument("resource_id", type=int)

    p_search = sub.add_parser("search", help="跨课程搜索资源(子串, 大小写不敏感)")
    p_search.add_argument("keyword", help="子串关键词, 匹配显示名或路径/URL")

    p_org = sub.add_parser("organize", help="整理目录: 默认只预览, --yes 才执行")
    p_org.add_argument("directory")
    p_org.add_argument("--yes", action="store_true", help="确认执行移动")

    p_log = sub.add_parser("log", help="查看操作留痕")
    p_log.add_argument("--limit", type=int, default=20)

    _build_schedule_subcommands(sub)
    return parser


def _build_schedule_subcommands(sub) -> None:
    """注册日程相关子命令(GUI 同源, 均为 Controller 动作的薄封装)."""
    p_sched = sub.add_parser("schedule", help="列周历日程(默认本周)")
    p_sched.add_argument("--week", type=int, help="学期第几周")
    p_sched.add_argument("--date", help="任意日期 YYYY-MM-DD(取所在周)")

    sub.add_parser("todos", help="列出待办")

    p_add_todo = sub.add_parser("add-todo", help="新建待办")
    p_add_todo.add_argument("title")
    p_add_todo.add_argument("--due", help="截止日期 YYYY-MM-DD")
    p_add_todo.add_argument("--course", default="", help="关联课程名")

    p_toggle = sub.add_parser("toggle-todo", help="按 id 翻转待办完成状态")
    p_toggle.add_argument("todo_id", type=int)

    p_import = sub.add_parser("import-schedule",
                              help="导入个人校历 html(替换课程段, 联排自动合并)")
    p_import.add_argument("path")

    p_add_event = sub.add_parser("add-event", help="手动添加日程(两种模式)")
    p_add_event.add_argument("title")
    p_add_event.add_argument("--location", default="")
    mode = p_add_event.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", help="按日期模式: YYYY-MM-DD")
    mode.add_argument("--weekday", type=int, help="按周数模式: 星期 1-7")
    p_add_event.add_argument("--start", help="开始 HH:MM(或用 --session)")
    p_add_event.add_argument("--end", help="结束 HH:MM(或用 --session)")
    p_add_event.add_argument("--session", type=int,
                             help="大节 1-6, 给出则按标准时段填充起止")
    p_add_event.add_argument("--weeks", help="周数模式: 周次列表, 如 1-8,11")

    p_edit = sub.add_parser("edit-event",
                            help="按序号修改日程(序号见 schedule 输出的 #N)")
    p_edit.add_argument("index", type=int)
    p_edit.add_argument("--title")
    p_edit.add_argument("--location")
    p_edit.add_argument("--start", help="HH:MM")
    p_edit.add_argument("--end", help="HH:MM")
    p_edit.add_argument("--date", help="仅本次模式可改日期 YYYY-MM-DD")
    p_edit.add_argument("--series", action="store_true",
                        help="应用到整个系列(标题/地点/起止, 日期不变)")

    p_rm = sub.add_parser("rm-event", help="按序号删除日程")
    p_rm.add_argument("index", type=int)
    p_rm.add_argument("--series", action="store_true", help="删除整个系列")


def _resource_status(row) -> str:
    """资源状态标签: 失效/已变更/ok."""
    exists, changed = Storage.check_resource(row)
    return "失效" if not exists else ("已变更" if changed else "ok")


def _print_resources(storage: Storage, course_id=None) -> None:
    """打印资源列表; course_id 为 None 时遍历全部课程."""
    courses = storage.list_courses()
    ids = [course_id] if course_id is not None else [c["id"] for c in courses]
    names = {c["id"]: c["name"] for c in courses}
    for cid in ids:
        print(f"== 课程 {cid} {names.get(cid, '(未知)')} ==")
        for res in storage.list_resources(cid):
            print(f"  #{res['id']} [{KIND_LABEL[res['kind']]}] {res['display_name']}"
                  f" | {_resource_status(res)} | {res['path_or_url']}")


def _cmd_search(ctl: Controller, args) -> int:
    """search 子命令: 跨课程按关键词找资源, 每行含课程名/类型/名称/状态/路径."""
    result = ctl.search_resources(args.keyword)
    if not result.ok:
        return _emit(result)
    rows = result.detail["rows"]
    keyword = args.keyword.strip()
    if not rows:
        print(f"没有匹配 \"{keyword}\" 的资源")
        return 0
    print(f"{result.message} \"{keyword}\":")
    for row in rows:
        print(f"  #{row['id']} [{KIND_LABEL[row['kind']]}] {row['display_name']}"
              f" | 课程: {row['course_name']} | {_resource_status(row)}"
              f" | {row['path_or_url']}")
    return 0


def _emit(result) -> int:
    """打印 ActionResult 并按成败给退出码."""
    print(result.message, file=sys.stderr if not result.ok else sys.stdout)
    return 0 if result.ok else 2


def _flatten_events(data):
    """(段名, Event) 全量列表; 序号与 schedule/edit-event/rm-event 一致."""
    return [("courses", e) for e in data.courses] + [("manual", e) for e in data.manual]


def _print_schedule(ctl: Controller, args) -> int:
    """列一周日程: 按天分组, 带全局序号(供 edit-event/rm-event 引用)."""
    start = ctl.semester_start()
    if args.week is not None:
        if not start:
            print("未配置 semester_start, 无法按周次查询", file=sys.stderr)
            return 2
        monday = week_start(start) + timedelta(weeks=args.week - 1)
    elif args.date:
        try:
            monday = week_start(date.fromisoformat(args.date))
        except ValueError:
            print("日期须为 YYYY-MM-DD", file=sys.stderr)
            return 2
    else:
        monday = week_start(date.today())
    data = ctl.schedule_snapshot()
    flat = _flatten_events(data)
    index_by_id = {id(e): i for i, (_s, e) in enumerate(flat)}
    print("== " + week_label(monday, start, ctl.semester_week_count()) + " ==")
    entries = entries_for_week(data, monday)
    shown = False
    for weekday in range(1, 8):
        if not entries[weekday]:
            continue
        day = monday + timedelta(days=weekday - 1)
        print(f"{WEEKDAY_NAMES[weekday - 1]} {day.strftime('%m/%d')}")
        for entry in entries[weekday]:
            index = index_by_id[id(entry.origin)] + 1
            marks = "[手动]" if entry.is_manual else ""
            if entry.origin and entry.origin.sid:
                marks += f"[系列#{entry.origin.sid}]"
            location = f" @ {entry.location}" if entry.location else ""
            end_text = entry.end.strftime("%H:%M") if entry.end else ""
            print(f"  #{index} {entry.start.strftime('%H:%M')}-{end_text} "
                  f"{entry.title}{location} {marks}".rstrip())
            shown = True
    if not shown:
        print("  (本周无日程)")
    return 0


def _print_todos(ctl: Controller) -> int:
    """列待办: 未完成在前, 状态标注(x 完成/! 逾期/今 今天到期)."""
    data = ctl.schedule_snapshot()
    todos = sorted_todos(data.todos)
    if not todos:
        print("(无待办)")
        return 0
    today = date.today()
    state_label = {"done": "x", "overdue": "!", "today": "今",
                   "upcoming": " ", "nodate": " "}
    for todo in todos:
        state = state_label[todo_status(todo, today)]
        due = f" | {todo.due.strftime('%m-%d')}" if todo.due else ""
        course = f" | {todo.course}" if todo.course else ""
        print(f"#{todo.tid} [{state}] {todo.title}{due}{course}")
    return 0


def _cmd_add_event(ctl: Controller, args) -> int:
    """添加手动日程: --date 单次, --weekday+--weeks 按周数展开为系列.

    参数校验错误与 ActionResult 失败统一走 stderr, 退出码 2(契约同 _emit).
    """
    try:
        start = end = None
        if args.session is not None:
            if not 1 <= args.session <= 6:
                raise ValueError("--session 须为 1-6")
            start, end = SESSION_TIMES[args.session]
        if args.start:
            start = time.fromisoformat(args.start)
        if args.end:
            end = time.fromisoformat(args.end)
        if start is None or end is None:
            raise ValueError("需 --session 或同时给 --start 与 --end")
        if end <= start:
            raise ValueError("结束时间须晚于开始时间")
        if args.date:
            events = [Event(date.fromisoformat(args.date), start, end,
                            args.title, args.location)]
        else:
            if not 1 <= args.weekday <= 7:
                raise ValueError("--weekday 须为 1-7")
            if not args.weeks:
                raise ValueError("周数模式需 --weeks")
            semester_start = ctl.semester_start()
            if not semester_start:
                raise ValueError("未配置 semester_start, 无法按周数展开")
            events = expand_weekly(semester_start,
                                   parse_week_list(args.weeks),
                                   args.weekday, start, end,
                                   args.title, args.location)
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2
    return _emit(ctl.add_manual(events))


def _pick_event(ctl: Controller, index: int):
    """按 schedule 显示的全局序号取 (段名, Event); 越界返回 None."""
    flat = _flatten_events(ctl.schedule_snapshot())
    if not 1 <= index <= len(flat):
        return None
    return flat[index - 1]


def _cmd_edit_event(ctl: Controller, args) -> int:
    """按序号修改日程(仅本次, 或 --series 应用到整个系列)."""
    picked = _pick_event(ctl, args.index)
    if picked is None:
        print("序号超出范围", file=sys.stderr)
        return 2
    section, old = picked
    if args.series and not old.sid:
        print("该日程不属于任何系列", file=sys.stderr)
        return 2
    try:
        new = Event(
            date.fromisoformat(args.date) if args.date else old.date,
            time.fromisoformat(args.start) if args.start else old.start,
            time.fromisoformat(args.end) if args.end else old.end,
            args.title if args.title is not None else old.title,
            args.location if args.location is not None else old.location,
            old.sid)
        if new.end <= new.start:
            raise ValueError("结束时间须晚于开始时间")
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2
    return _emit(ctl.update_event(section, old, new, whole_series=args.series))


def _cmd_rm_event(ctl: Controller, args) -> int:
    """按序号删除日程(本次, 或 --series 删除整个系列)."""
    picked = _pick_event(ctl, args.index)
    if picked is None:
        print("序号超出范围", file=sys.stderr)
        return 2
    section, old = picked
    return _emit(ctl.remove_event(section, old, whole_series=args.series))


_WORKSPACE_COMMANDS = ("courses", "add-course", "del-course", "add",
                       "resources", "remove", "search", "organize", "log")


def run_command(ctl: Controller, args) -> int:
    """执行一条已解析的命令, 返回退出码. 不持有连接, 由调用方管理."""
    if args.command in _WORKSPACE_COMMANDS:
        return _run_workspace_command(ctl, args)
    if args.command:
        return _run_schedule_command(ctl, args)
    return 0


def _run_workspace_command(ctl: Controller, args) -> int:
    """执行工作区相关子命令."""
    if args.command == "courses":
        for course in ctl.storage.list_courses():
            print(f"#{course['id']} {course['name']}")
    elif args.command == "add-course":
        return _emit(ctl.add_course(args.name))
    elif args.command == "del-course":
        return _emit(ctl.delete_course(args.course_id))
    elif args.command == "add":
        return _emit(ctl.add_resource(args.course_id, args.target))
    elif args.command == "resources":
        _print_resources(ctl.storage, args.course_id)
    elif args.command == "remove":
        return _emit(ctl.remove_resource(args.resource_id))
    elif args.command == "search":
        return _cmd_search(ctl, args)
    elif args.command == "organize":
        return _cmd_organize(ctl, args)
    else:  # log
        for op in ctl.storage.list_operations(args.limit):
            print(f"#{op['id']} [{op['status']}] {op['action']}"
                  f" | {op['path_snapshot'] or '-'} | {op['detail'] or ''}")
    return 0




def _cmd_todo(ctl: Controller, args) -> int:
    """add-todo / toggle-todo."""
    if args.command == "toggle-todo":
        return _emit(ctl.toggle_todo(args.todo_id))
    due = None
    if args.due:
        try:
            due = date.fromisoformat(args.due)
        except ValueError:
            print("--due 须为 YYYY-MM-DD", file=sys.stderr)
            return 2
    return _emit(ctl.add_todo(args.title, due, args.course))


def _cmd_edit_or_rm(ctl: Controller, args) -> int:
    """edit-event / rm-event."""
    if args.command == "edit-event":
        return _cmd_edit_event(ctl, args)
    return _cmd_rm_event(ctl, args)


def _run_schedule_command(ctl: Controller, args) -> int:
    """执行日程相关子命令(GUI 同源动作的薄封装)."""
    if args.command == "schedule":
        return _print_schedule(ctl, args)
    if args.command == "todos":
        return _print_todos(ctl)
    if args.command in ("add-todo", "toggle-todo"):
        return _cmd_todo(ctl, args)
    if args.command == "import-schedule":
        return _emit(ctl.import_courses(args.path))
    if args.command in ("edit-event", "rm-event"):
        return _cmd_edit_or_rm(ctl, args)
    return _cmd_add_event(ctl, args)


def main(argv=None) -> int:
    """CLI 入口: 带子命令则单次执行, 不带则进入交互模式."""
    args = build_parser().parse_args(argv)
    storage = Storage(args.db)
    try:
        ctl = Controller(storage, schedule_path=args.schedule)
        if args.command is None:
            return repl(ctl)
        return run_command(ctl, args)
    finally:
        storage.close()


def repl(ctl: Controller) -> int:
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
            run_command(ctl, args)
        else:
            parser.print_help()


def _cmd_organize(ctl: Controller, args) -> int:
    """整理子命令: 先预览, --yes 才执行并留痕."""
    preview = ctl.preview_organize(args.directory)
    if not preview.ok:
        print(preview.message, file=sys.stderr)
        return 2
    plan = preview.detail["plan"]
    for item in plan:
        print(f"  {item.src} -> {item.dst}")
    if not args.yes:
        print(f"共 {len(plan)} 项(预览). 加 --yes 执行.")
        return 0
    result = ctl.execute_organize(args.directory, plan)
    code = _emit(result)
    for err in result.detail.get("errors", []):
        print(f"  失败: {err}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
