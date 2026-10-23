"""日程纯逻辑: YAML 读写与校验, 周计算, 条目聚合, TODO 排序与状态.

不依赖 Qt, GUI 与未来 CLI 共用(对齐 organize.py). 数据文件为用户可编辑的
纯 YAML; GUI 保存(new/toggle todo)会全量重写该文件, 手写注释会丢失.

schema 三段:
  courses: [{date: "YYYY-MM-DD", start: "HH:MM", end: "HH:MM", title,
             location?}]   # 导入的课程数据(个人校历 html, 一般不手编)
  manual:  [{date, start, end, title, location?}]  # 手动添加的事件(预留)
  todos:   [{id, title, due?, course?, done}]      # id 程序维护, 缺失自动补号
"""

import os
import shutil
from dataclasses import dataclass, field
from datetime import date, time, timedelta

import yaml

HEADER = ("# 水木工作台日程数据(courses=导入课程, manual=手动事件, todos=待办).\n"
          "# 注意: GUI 保存会重写本文件, 手写注释会丢失.\n")


@dataclass
class Event:
    """一次有确切日期与起止时间的事件(课程或手动添加)."""

    date: date
    start: time
    end: time = None
    title: str = ""
    location: str = ""


@dataclass
class Todo:
    """任务备忘条目."""

    tid: int
    title: str
    due: date = None
    course: str = ""
    done: bool = False


@dataclass
class DayEntry:
    """周历列内单张卡片的统一投影."""

    start: time
    end: time = None
    title: str = ""
    location: str = ""
    is_manual: bool = False  # True=手动事件, False=导入课程


@dataclass
class ScheduleData:
    """一次完整加载的日程."""

    courses: list = field(default_factory=list)
    manual: list = field(default_factory=list)
    todos: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---- 解析(私有, 单测直接覆盖) ----

def _parse_time(raw) -> time:
    """'HH:MM' 字符串转 time, 非法抛 ValueError."""
    if not isinstance(raw, str) or raw.count(":") != 1:
        raise ValueError(f"时间须为 HH:MM 字符串: {raw!r}")
    hh, mm = raw.split(":")
    return time(int(hh), int(mm))  # 越界由 int/time 抛 ValueError


def _parse_date(raw) -> date:
    """YYYY-MM-DD 字符串或 date 对象转 date(YAML 未加引号会给出 date)."""
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"日期须为 YYYY-MM-DD: {raw!r}")
    return date.fromisoformat(raw)


def _parse_event(raw) -> Event:
    """解析 courses/manual 条目, 非法抛 ValueError(带原因)."""
    if not isinstance(raw, dict):
        raise ValueError("条目须为映射")
    start = _parse_time(raw.get("start"))
    end = None
    if raw.get("end") is not None:
        end = _parse_time(raw.get("end"))
        if end <= start:
            raise ValueError("end 须晚于 start")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title 须为非空字符串")
    location = raw.get("location", "")
    if not isinstance(location, str):
        raise ValueError("location 须为字符串")
    return Event(_parse_date(raw.get("date")), start, end,
                 title.strip(), location)


def _parse_todo(raw, fallback_tid: int) -> Todo:
    """解析 todos 条目; id 缺失/非法用 fallback_tid 占位."""
    if not isinstance(raw, dict):
        raise ValueError("条目须为映射")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title 须为非空字符串")
    due = None
    if raw.get("due") is not None:
        due = _parse_date(raw.get("due"))
    course = raw.get("course", "")
    if not isinstance(course, str):
        raise ValueError("course 须为字符串")
    done = raw.get("done", False)
    if not isinstance(done, bool):
        raise ValueError("done 须为 true/false")
    tid = raw.get("id", fallback_tid)
    if not isinstance(tid, int) or isinstance(tid, bool) or tid < 1:
        tid = fallback_tid
    return Todo(tid, title.strip(), due, course, done)


def _fill_todo_ids(parsed: list) -> list:
    """缺失/重复的 tid(占位 0)按顺序补成未占用的最小正整数."""
    seen = set()
    for todo in parsed:
        if todo.tid >= 1 and todo.tid not in seen:
            seen.add(todo.tid)
        else:
            todo.tid = 0
    candidate = 1
    for todo in parsed:
        if todo.tid == 0:
            while candidate in seen:
                candidate += 1
            todo.tid = candidate
            seen.add(candidate)
    return parsed


# ---- 读写与播种 ----

def load_schedule(path: str) -> ScheduleData:
    """读取并校验日程 YAML.

    文件缺失返回空(播种归 ensure_schedule_file); YAML 损坏或顶层非映射
    整体回退为空并记一条 warning; 单条目非法跳过并记 warning(带段落与序号).
    旧版 timetable/events 段不再使用, 静默忽略.
    """
    data = ScheduleData()
    if not os.path.isfile(path):
        return data
    try:
        with open(path, encoding="utf-8") as f:
            root = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        data.warnings.append(f"日程文件不可读, 已回退为空: {exc}")
        return data
    if not isinstance(root, dict):
        data.warnings.append("日程文件顶层须为映射, 已回退为空")
        return data

    def section(name):
        raw = root.get(name)
        if raw is None:
            return []
        if not isinstance(raw, list):
            data.warnings.append(f"'{name}' 段不是列表, 已忽略")
            return []
        return raw

    for name in ("courses", "manual"):
        target = data.courses if name == "courses" else data.manual
        for i, raw in enumerate(section(name), 1):
            try:
                target.append(_parse_event(raw))
            except ValueError as exc:
                data.warnings.append(f"{name} 第 {i} 条跳过: {exc}")

    parsed = []
    for i, raw in enumerate(section("todos"), 1):
        try:
            parsed.append(_parse_todo(raw, fallback_tid=0))
        except ValueError as exc:
            data.warnings.append(f"todos 第 {i} 条跳过: {exc}")
    data.todos = _fill_todo_ids(parsed)
    return data


def save_schedule(path: str, data: ScheduleData) -> None:
    """全量重写日程文件. 原子替换: 先写 .tmp 再 os.replace.

    date/time 序列化为字符串; OSError 由调用方(Controller)处理.
    """

    def dump_events(entries):
        return [
            {"date": e.date.strftime("%Y-%m-%d"),
             "start": e.start.strftime("%H:%M"),
             **({"end": e.end.strftime("%H:%M")} if e.end else {}),
             "title": e.title,
             **({"location": e.location} if e.location else {})}
            for e in entries
        ]

    root = {
        "courses": dump_events(data.courses),
        "manual": dump_events(data.manual),
        "todos": [
            {"id": t.tid, "title": t.title,
             **({"due": t.due.strftime("%Y-%m-%d")} if t.due else {}),
             **({"course": t.course} if t.course else {}),
             "done": t.done}
            for t in data.todos
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump(root, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def ensure_schedule_file(path: str, template_path: str = None) -> bool:
    """日程文件缺失时播种(优先复制模板), 返回是否执行了写入. 幂等.

    模板缺失/不可复制时退化为写入仅含头部说明与空三段的骨架.
    """
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if template_path and os.path.isfile(template_path):
        try:
            shutil.copyfile(template_path, path)
            return True
        except OSError:
            pass  # 复制失败, 落到写骨架
    save_schedule(path, ScheduleData())
    return True


# ---- 周计算与聚合 ----

def week_start(day: date) -> date:
    """day 所在周的周一."""
    return day - timedelta(days=day.weekday())


def semester_week(day: date, start: date) -> int:
    """学期周次 = (day - start) // 7 + 1. 负数原样返回."""
    return (day - start).days // 7 + 1


def clamp_to_semester(monday: date, start: date, week_count: int) -> date:
    """把周一夹在学期范围 [第 1 周周一, 第 week_count 周周一] 内.

    week_count < 1(未配置)时原样返回; start 非周一时对齐到所在周周一.
    """
    if week_count < 1:
        return monday
    first = week_start(start)
    last = first + timedelta(days=(week_count - 1) * 7)
    return min(max(monday, first), last)


def entries_for_week(data: ScheduleData, monday: date) -> dict:
    """返回 {weekday(1-7): 按开始时间排序的 DayEntry 列表}.

    courses 与 manual 中的事件按 date 落在 [monday, monday+6] 归入对应列.
    """
    result = {wd: [] for wd in range(1, 8)}

    def place(entries, is_manual):
        for entry in entries:
            offset = (entry.date - monday).days
            if 0 <= offset <= 6:
                result[offset + 1].append(DayEntry(
                    entry.start, entry.end, entry.title, entry.location,
                    is_manual))

    place(data.manual, True)
    place(data.courses, False)
    for entries in result.values():
        entries.sort(key=lambda e: (e.start, e.title))
    return result


# ---- TODO 排序/状态/变更(纯内存, 落盘归 Controller) ----

def sorted_todos(todos: list) -> list:
    """排序: 未完成在前(有截止且早者在前, 无截止随后), 已完成置底; tid 决胜."""
    def key(todo):
        due = todo.due if todo.due is not None else date.max
        no_due = todo.due is None
        return (todo.done, no_due, due, todo.tid)

    return sorted(todos, key=key)


def todo_status(todo: Todo, today: date) -> str:
    """状态: done | overdue | today | upcoming | nodate."""
    if todo.done:
        return "done"
    if todo.due is None:
        return "nodate"
    if todo.due < today:
        return "overdue"
    if todo.due == today:
        return "today"
    return "upcoming"


def next_todo_id(data: ScheduleData) -> int:
    """现有最大 id + 1, 空列表返回 1."""
    return max((t.tid for t in data.todos), default=0) + 1


def add_todo(data: ScheduleData, title: str, due, course: str) -> Todo:
    """内存中追加新 todo 并返回(空标题校验归 Controller)."""
    todo = Todo(next_todo_id(data), title, due, course, False)
    data.todos.append(todo)
    return todo


def toggle_todo(data: ScheduleData, tid: int):
    """按 id 翻转 done, 返回更新后的 todo; 找不到返回 None."""
    for todo in data.todos:
        if todo.tid == tid:
            todo.done = not todo.done
            return todo
    return None
