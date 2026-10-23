"""schedule.py 单元测试: 解析校验, 周计算, 聚合投影, TODO 排序与变更."""

import os
from datetime import date, time

from shuimu.schedule import (
    DayEntry,
    Event,
    ScheduleData,
    TimetableSlot,
    Todo,
    add_todo,
    ensure_schedule_file,
    entries_for_week,
    load_schedule,
    next_todo_id,
    save_schedule,
    semester_week,
    sorted_todos,
    toggle_todo,
    todo_status,
    week_start,
)


def _slot(**kwargs):
    """快速构造合法课表条目."""
    base = {"weekday": 1, "start": time(8, 0), "end": time(9, 35),
            "title": "高数", "location": "", "weeks": None}
    base.update(kwargs)
    return TimetableSlot(**base)


def _todo(**kwargs):
    """快速构造 todo."""
    base = {"tid": 1, "title": "任务", "due": None, "course": "", "done": False}
    base.update(kwargs)
    return Todo(**base)


class TestLoadSchedule:
    """加载与容错."""

    def test_missing_file_returns_empty(self, tmp_path):
        """文件缺失返回空且无警告."""
        data = load_schedule(str(tmp_path / "nope.yaml"))
        assert not data.timetable and not data.events and not data.todos
        assert not data.warnings

    def test_broken_yaml_falls_back(self, tmp_path):
        """整体损坏回退为空并记一条警告."""
        f = tmp_path / "s.yaml"
        f.write_text("{{{not yaml", encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.timetable and not data.todos
        assert len(data.warnings) == 1

    def test_non_dict_root_falls_back(self, tmp_path):
        """顶层非映射回退为空."""
        f = tmp_path / "s.yaml"
        f.write_text("- a\n- b\n", encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.todos
        assert len(data.warnings) == 1

    def test_section_type_error_ignored(self, tmp_path):
        """段类型错(不是列表)该段置空并警告, 其余正常."""
        f = tmp_path / "s.yaml"
        f.write_text("timetable: \"oops\"\ntodos: []\n", encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.timetable and not data.todos
        assert len(data.warnings) == 1

    def test_invalid_entries_skipped_with_warning(self, tmp_path):
        """单条目非法跳过并带段落与序号."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "timetable:\n"
            "  - {weekday: 0, start: '08:00', end: '09:00', title: x}\n"
            "  - {weekday: 2, start: '10:00', end: '11:00', title: ok}\n"
            "todos:\n"
            "  - {title: good}\n"
            "  - {title: '', id: 5}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert len(data.timetable) == 1 and data.timetable[0].weekday == 2
        assert len(data.todos) == 1 and data.todos[0].title == "good"
        assert len(data.warnings) == 2
        assert "timetable 第 1 条" in data.warnings[0]
        assert "todos 第 2 条" in data.warnings[1]

    def test_bad_time_and_date_and_end(self, tmp_path):
        """时间/日期非法, end<=start 均跳过."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "timetable:\n"
            "  - {weekday: 1, start: '25:00', end: '26:00', title: x}\n"
            "  - {weekday: 1, start: '10:00', end: '09:00', title: y}\n"
            "events:\n"
            "  - {date: '2026-02-30', start: '08:00', title: z}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.timetable and not data.events
        assert len(data.warnings) == 3

    def test_bad_weeks_skipped(self, tmp_path):
        """weeks 起>止或非正整数跳过."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "timetable:\n"
            "  - {weekday: 1, start: '08:00', end: '09:00', title: a, weeks: [5, 2]}\n"
            "  - {weekday: 1, start: '08:00', end: '09:00', title: b, weeks: 3}\n"
            "  - {weekday: 1, start: '08:00', end: '09:00', title: c, weeks: [1, 16]}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert len(data.timetable) == 1
        assert data.timetable[0].weeks == (1, 16)
        assert len(data.warnings) == 2

    def test_todo_ids_filled_and_deduped(self, tmp_path):
        """缺失/非法/重复 id 补成不冲突的正整数."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "todos:\n"
            "  - {title: a, id: 2}\n"
            "  - {title: b}\n"          # 缺 id
            "  - {title: c, id: 2}\n"    # 重复
            "  - {title: d, id: -1}\n",  # 非法
            encoding="utf-8")
        data = load_schedule(str(f))
        ids = [t.tid for t in data.todos]
        assert ids[0] == 2
        assert sorted(ids) == [1, 2, 3, 4]
        assert len(set(ids)) == 4

    def test_unquoted_yaml_dates_accepted(self, tmp_path):
        """未加引号的日期解析成 date 对象, 时间保持字符串, 两者均可解析."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "events:\n"
            "  - {date: 2026-10-27, start: '08:00', title: t}\n"
            "timetable:\n"
            "  - {weekday: 1, start: 08:00, end: 09:00, title: u}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert len(data.events) == 1 and data.events[0].date == date(2026, 10, 27)
        assert len(data.timetable) == 1  # 本环境 PyYAML 不做六十进制转换
        assert not data.warnings


class TestSaveSchedule:
    """保存与往返."""

    def test_round_trip(self, tmp_path):
        """save 后 load 得到等价数据(忽略 warnings)."""
        data = ScheduleData(
            timetable=[_slot(weeks=(1, 8), location="三教")],
            events=[Event(date(2026, 10, 27), time(19), time(21), "讲座", "礼堂")],
            todos=[_todo(tid=3, title="作业", due=date(2026, 10, 26),
                         course="微积分", done=True)],
        )
        path = str(tmp_path / "s.yaml")
        save_schedule(path, data)
        loaded = load_schedule(path)
        assert loaded.timetable == data.timetable
        assert loaded.events == data.events
        assert loaded.todos == data.todos
        assert not loaded.warnings

    def test_atomic_no_tmp_leftover(self, tmp_path):
        """保存后无 .tmp 残留, 头部说明存在."""
        path = str(tmp_path / "s.yaml")
        save_schedule(path, ScheduleData())
        assert not os.path.exists(path + ".tmp")
        with open(path, encoding="utf-8") as f:
            assert "GUI 保存会重写" in f.read()


class TestEnsureScheduleFile:
    """播种."""

    def test_seeds_from_template(self, tmp_path):
        """模板存在时复制模板."""
        tpl = tmp_path / "tpl.yaml"
        tpl.write_text("todos:\n  - {title: x}\n", encoding="utf-8")
        path = str(tmp_path / "data" / "s.yaml")
        assert ensure_schedule_file(path, str(tpl)) is True
        assert load_schedule(path).todos[0].title == "x"
        assert ensure_schedule_file(path, str(tpl)) is False  # 幂等

    def test_seeds_skeleton_without_template(self, tmp_path):
        """模板缺失时写骨架(头部+空三段)."""
        path = str(tmp_path / "s.yaml")
        assert ensure_schedule_file(path) is True
        data = load_schedule(path)
        assert not data.timetable and not data.warnings


class TestWeekHelpers:
    """周计算."""

    def test_week_start(self):
        """周一当天与周日都归到同一周一."""
        assert week_start(date(2026, 10, 19)) == date(2026, 10, 19)  # 周一
        assert week_start(date(2026, 10, 25)) == date(2026, 10, 19)  # 周日

    def test_week_start_cross_month_year(self):
        """跨月与跨年."""
        assert week_start(date(2026, 1, 1)) == date(2025, 12, 29)
        assert week_start(date(2026, 12, 31)) == date(2026, 12, 28)

    def test_semester_week(self):
        """学期周计算, start 非周一仅产生平移."""
        start = date(2026, 9, 14)
        assert semester_week(date(2026, 9, 14), start) == 1
        assert semester_week(date(2026, 9, 20), start) == 1   # 第 1 周周日
        assert semester_week(date(2026, 9, 21), start) == 2
        assert semester_week(date(2026, 10, 23), start) == 6
        assert semester_week(date(2026, 9, 13), start) == 0   # 学期前


class TestEntriesForWeek:
    """周历聚合."""

    def test_timetable_and_events_by_column(self):
        """课表按 weekday 归列, 事件按日期归列, 按 start 排序."""
        data = ScheduleData(
            timetable=[_slot(weekday=2, start=time(19, 20), end=time(20, 55),
                             title="AI 课")],
            events=[Event(date(2026, 10, 23), time(14), None, "组会", "实验室")],
        )
        monday = date(2026, 10, 19)
        result = entries_for_week(data, monday, None)
        assert [e.title for e in result[2]] == ["AI 课"]
        assert result[2][0].is_event is False
        assert [e.title for e in result[5]] == ["组会"]  # 10-23 是周五
        assert result[5][0].is_event is True and result[5][0].end is None
        assert result[1] == [] and result[7] == []

    def test_sorted_by_start_stable(self):
        """同列多条目按 (start, title) 排序."""
        data = ScheduleData(timetable=[
            _slot(weekday=1, start=time(15), end=time(16), title="b 课"),
            _slot(weekday=1, start=time(8), end=time(9), title="a 课"),
            _slot(weekday=1, start=time(8), end=time(9), title="c 课"),
        ])
        result = entries_for_week(data, date(2026, 10, 19), None)
        assert [e.title for e in result[1]] == ["a 课", "c 课", "b 课"]

    def test_weeks_filter(self):
        """周次命中显示, 不命中过滤."""
        data = ScheduleData(timetable=[
            _slot(title="前半学期", weeks=(1, 8)),
            _slot(title="整学期", weeks=None),
        ])
        sem = date(2026, 9, 14)
        week6 = entries_for_week(data, date(2026, 10, 19), sem)
        assert [e.title for e in week6[1]] == ["前半学期", "整学期"]
        week10 = entries_for_week(data, date(2026, 11, 16), sem)
        assert [e.title for e in week10[1]] == ["整学期"]

    def test_weeks_without_semester_start_shows_all(self):
        """weeks 有值但学期未配置: 不过滤, 只记一次警告."""
        data = ScheduleData(timetable=[_slot(title="课", weeks=(1, 8))])
        monday = date(2026, 10, 19)
        entries_for_week(data, monday, None)
        entries_for_week(data, monday, None)
        assert len(data.warnings) == 1
        result = entries_for_week(data, date(2026, 12, 28), None)
        assert len(result[1]) == 1  # 仍显示


class TestTodoLogic:
    """TODO 排序/状态/变更."""

    def test_sorted_todos(self):
        """未完成在前(有截止早者前, 无截止随后), 完成置底, tid 决胜."""
        today = date(2026, 10, 23)
        todos = [
            _todo(tid=1, title="无截止"),
            _todo(tid=2, title="明天", due=date(2026, 10, 24)),
            _todo(tid=3, title="已完成", done=True),
            _todo(tid=4, title="昨天逾期", due=date(2026, 10, 22)),
        ]
        order = [t.title for t in sorted_todos(todos)]
        assert order == ["昨天逾期", "明天", "无截止", "已完成"]

    def test_todo_status_five_states(self):
        """done/overdue/today/upcoming/nodate."""
        today = date(2026, 10, 23)
        assert todo_status(_todo(done=True, due=today), today) == "done"
        assert todo_status(_todo(due=today), today) == "today"
        assert todo_status(_todo(due=date(2026, 10, 22)), today) == "overdue"
        assert todo_status(_todo(due=date(2026, 10, 24)), today) == "upcoming"
        assert todo_status(_todo(due=None), today) == "nodate"

    def test_add_and_toggle_and_next_id(self):
        """id 递增, 内存追加与翻转, 找不到返回 None."""
        data = ScheduleData()
        todo = add_todo(data, "任务 A", date(2026, 10, 26), "微积分")
        assert todo.tid == 1 and next_todo_id(data) == 2
        add_todo(data, "任务 B", None, "")
        flipped = toggle_todo(data, 1)
        assert flipped.done is True
        assert toggle_todo(data, 1).done is False
        assert toggle_todo(data, 99) is None


def test_day_entry_defaults():
    """DayEntry 默认值(供 GUI 使用)."""
    entry = DayEntry(time(8), None, "t", "", False)
    assert entry.end is None and not entry.is_event
