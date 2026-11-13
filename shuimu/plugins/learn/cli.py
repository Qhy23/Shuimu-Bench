"""网络学堂插件的 CLI 增强: learn-* 子命令的注册, 执行与列表打印.

学堂专属逻辑跟插件包走, cli/main.py 只做两行接线(注册与分发). 内置
服务型插件可带此类宿主接线(与 GUI 学堂页签同款边界), 外部插件只能经
通用 plugin-run 入口.
"""

import os
import sys

from shuimu.core.controller import Controller
from shuimu.core.learn import parse_learn_date

LEARN_COMMANDS = ("learn-status", "learn-refresh", "learn-courses",
                  "learn-homework", "learn-files", "learn-notices",
                  "learn-download")

_HOMEWORK_STATE_LABELS = (("pending", "未交"), ("submitted", "已交未批"),
                          ("graded", "已批"))


def build_learn_subcommands(sub) -> None:
    """注册网络学堂相关子命令(controller 学堂动作的薄封装)."""
    sub.add_parser("learn-status", help="学堂缓存概况(零联网)")
    p_refresh = sub.add_parser(
        "learn-refresh",
        help="全量刷新学堂缓存(需启用 learn 插件并已校园登录)")
    p_refresh.add_argument("--no-todos", action="store_true",
                           help="刷新后不同步未交作业进日程待办")
    sub.add_parser("learn-courses", help="列出缓存的学堂课程(含关联状态)")

    p_hw = sub.add_parser("learn-homework", help="列出作业三态")
    p_hw.add_argument("wlkcid", nargs="?", help="课程 id(缺省全部课程)")

    p_files = sub.add_parser("learn-files", help="列出课件(含已下载标记)")
    p_files.add_argument("wlkcid", help="课程 id")

    p_notices = sub.add_parser("learn-notices", help="列出公告")
    p_notices.add_argument("wlkcid", help="课程 id")

    p_dl = sub.add_parser("learn-download",
                          help="下载课件/附件并登记进关联课程")
    p_dl.add_argument("--kind", choices=["file", "homework", "notice"],
                      default="file", help="文件类别(默认课件)")
    p_dl.add_argument("--id", dest="file_id", required=True,
                      help="课件 wjid / 作业 zyid / 公告 ggid")
    p_dl.add_argument("--wlkcid", help="课程 id(homework/notice 必填)")
    p_dl.add_argument("--out", dest="out_dir", help="下载目录(缺省用配置)")
    p_dl.add_argument("--overwrite", action="store_true",
                      help="已下载的强制重下")


def run_learn_command(ctl: Controller, args) -> int:
    """执行学堂相关子命令; 供 cli/main.py 的分发调用."""
    if args.command == "learn-status":
        return _emit(ctl.learn_status())
    if args.command == "learn-refresh":
        result = ctl.learn_refresh(sync_todos=not args.no_todos)
        code = _emit(result)
        for err in result.detail.get("errors") or []:
            print(f"  失败: {err}", file=sys.stderr)
        if result.detail.get("state") == "expired":
            print("重新登录: campus-login 后重试", file=sys.stderr)
        return code
    if args.command == "learn-courses":
        return print_learn_courses(ctl)
    if args.command == "learn-homework":
        return print_learn_homework(ctl, args.wlkcid)
    if args.command == "learn-files":
        return print_learn_files(ctl, args.wlkcid)
    if args.command == "learn-notices":
        return print_learn_notices(ctl, args.wlkcid)
    return _emit(ctl.learn_download(
        args.kind, args.file_id, wlkcid=args.wlkcid,
        out_dir=args.out_dir, overwrite=args.overwrite))


def _emit(result) -> int:
    """打印 ActionResult 并按成败给退出码(与 cli.main._emit 同契约)."""
    print(result.message, file=sys.stderr if not result.ok else sys.stdout)
    return 0 if result.ok else 2


def _fmt_size(size) -> str:
    """字节数 -> 人类可读."""
    value = int(size or 0)
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} B"


def _learn_kcms(ctl: Controller) -> dict:
    """wlkcid -> 课名."""
    return {row["wlkcid"]: row["kcm"]
            for row in ctl.storage.learn_list_courses()}


def print_learn_courses(ctl: Controller) -> int:
    """learn-courses: 缓存课程与本地关联状态."""
    rows = ctl.storage.learn_list_courses()
    if not rows:
        print("(空: 先运行 learn-refresh)")
        return 0
    names = {c["id"]: c["name"] for c in ctl.storage.list_courses()}
    for row in rows:
        link = names.get(row["course_id"]) if row["course_id"] else None
        tag = f" -> {link}" if link else "(未关联)"
        print(f"{row['wlkcid']}  {row['kcm']} {tag}")
        print(f"    课号 {row['kch'] or '-'} 课序 {row['kxh'] or '-'}"
              f" 教师 {row['jsm'] or '-'}")
    return 0


def print_learn_homework(ctl: Controller, wlkcid=None) -> int:
    """learn-homework: 按课分组的作业三态."""
    rows = ctl.storage.learn_list_homework(wlkcid=wlkcid)
    if not rows:
        print("(空: 先运行 learn-refresh)")
        return 0
    kcms = _learn_kcms(ctl)
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row["wlkcid"], []).append(row)
    for wid, items in grouped.items():
        print(f"== {kcms.get(wid, wid)} ==")
        for state, label in _HOMEWORK_STATE_LABELS:
            matches = [i for i in items if i["state"] == state]
            if not matches:
                continue
            print(f"  [{label}] {len(matches)}")
            for item in matches:
                extra = ""
                if state == "pending":
                    extra = f"截止 {parse_learn_date(item['jzsj']) or '-'}"
                    if item["sfbj"] == "1":
                        extra += (f"(补交至 "
                                  f"{parse_learn_date(item['bjjzsj']) or '-'})")
                elif state == "submitted":
                    extra = f"提交 {parse_learn_date(item['scsj']) or '-'}"
                else:
                    extra = f"成绩 {item['cj'] or '-'}"
                print(f"    {item['zyid']}  {item['bt']} | {extra}")
    return 0


def print_learn_files(ctl: Controller, wlkcid: str) -> int:
    """learn-files: 课件列表与已下载标记."""
    rows = ctl.storage.learn_list_files(wlkcid)
    if not rows:
        print("(空: 先运行 learn-refresh)")
        return 0
    for row in rows:
        downloaded = ""
        if row["wjid"]:
            record = ctl.storage.learn_find_download("file", row["wjid"])
            if record is not None and os.path.exists(record["path"]):
                downloaded = " [已下载]"
        print(f"{row['wjid'] or '-'}  {row['bt']}{downloaded}")
        print(f"    {row['category'] or '未分类'} | "
              f"{_fmt_size(row['wjdx'])} | {row['scsj'] or '-'}")
    return 0


def print_learn_notices(ctl: Controller, wlkcid: str) -> int:
    """learn-notices: 公告列表."""
    rows = ctl.storage.learn_list_notices(wlkcid)
    if not rows:
        print("(空: 先运行 learn-refresh)")
        return 0
    for row in rows:
        flags = "已读" if row["sfyd"] == "1" else "未读"
        attach = f" 附件: {row['fjmc']}" if row["fjmc"] else ""
        print(f"{row['ggid']}  {row['bt']} [{flags}]{attach}")
        print(f"    发布 {row['fbsj'] or '-'} 截止 {row['jzsj'] or '-'}")
    return 0
