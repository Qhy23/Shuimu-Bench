"""schedule.py 单元测试: 解析校验, 周计算, 聚合投影, TODO 排序与变更."""

import os
from datetime import date, time

from shuimu.schedule import (
    DayEntry,
    clamp_to_semester,
    Event,
    ScheduleData,
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


def _event(**kwargs):
    """快速构造事件(默认 2026-10-26 周一 08:00-09:35)."""
    base = {"date": date(2026, 10, 26), "start": time(8), "end": time(9, 35),
            "title": "课", "location": ""}
    base.update(kwargs)
    return Event(**base)


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
        assert not data.courses and not data.manual and not data.todos
        assert not data.warnings

    def test_broken_yaml_falls_back(self, tmp_path):
        """整体损坏回退为空并记一条警告."""
        f = tmp_path / "s.yaml"
        f.write_text("{{{not yaml", encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.courses and not data.todos
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
        f.write_text("courses: \"oops\"\ntodos: []\n", encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.courses and not data.todos
        assert len(data.warnings) == 1

    def test_legacy_sections_ignored(self, tmp_path):
        """旧版 timetable/events 段静默忽略, 不报警告."""
        f = tmp_path / "s.yaml"
        f.write_text("timetable:\n  - {weekday: 1}\nevents: []\ntodos: []\n",
                     encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.courses and not data.warnings

    def test_invalid_entries_skipped_with_warning(self, tmp_path):
        """courses/manual/todos 单条非法跳过并带段落与序号."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "courses:\n"
            "  - {date: '2026-10-26', start: '25:00', end: '26:00', title: x}\n"
            "manual:\n"
            "  - {date: '2026-10-27', start: '10:00', end: '09:00', title: y}\n"
            "todos:\n"
            "  - {title: good}\n"
            "  - {title: '', id: 5}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.courses and not data.manual
        assert len(data.todos) == 1 and data.todos[0].title == "good"
        assert len(data.warnings) == 3
        assert "courses 第 1 条" in data.warnings[0]
        assert "manual 第 1 条" in data.warnings[1]
        assert "todos 第 2 条" in data.warnings[2]

    def test_bad_date_skipped(self, tmp_path):
        """非法日期跳过."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "courses:\n  - {date: '2026-02-30', start: '08:00', end: '09:00', title: z}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert not data.courses and len(data.warnings) == 1

    def test_manual_parsed_like_courses(self, tmp_path):
        """manual 段与 courses 同构解析."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "manual:\n"
            "  - {date: '2026-10-27', start: '19:00', end: '21:00', title: 讲座, location: 礼堂}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert len(data.manual) == 1
        assert data.manual[0].location == "礼堂"

    def test_todo_ids_filled_and_deduped(self, tmp_path):
        """缺失/非法/重复 id 补成不冲突的正整数."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "todos:\n"
            "  - {title: a, id: 2}\n"
            "  - {title: b}\n"
            "  - {title: c, id: 2}\n"
            "  - {title: d, id: -1}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        ids = [t.tid for t in data.todos]
        assert ids[0] == 2
        assert sorted(ids) == [1, 2, 3, 4]
        assert len(set(ids)) == 4

    def test_unquoted_yaml_dates_accepted(self, tmp_path):
        """未加引号的日期解析成 date 对象, 时间保持字符串, 均可解析."""
        f = tmp_path / "s.yaml"
        f.write_text(
            "courses:\n"
            "  - {date: 2026-10-27, start: 08:00, end: 09:35, title: t}\n",
            encoding="utf-8")
        data = load_schedule(str(f))
        assert len(data.courses) == 1
        assert data.courses[0].date == date(2026, 10, 27)
        assert not data.warnings


class TestSaveSchedule:
    """保存与往返."""

    def test_round_trip(self, tmp_path):
        """save 后 load 得到等价数据."""
        data = ScheduleData(
            courses=[_event(location="一教101")],
            manual=[_event(date=date(2026, 10, 27), start=time(19),
                          end=time(21), title="讲座", location="礼堂")],
            todos=[_todo(tid=3, title="作业", due=date(2026, 10, 26),
                         course="微积分", done=True)],
        )
        path = str(tmp_path / "s.yaml")
        save_schedule(path, data)
        loaded = load_schedule(path)
        assert loaded.courses == data.courses
        assert loaded.manual == data.manual
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
        assert not data.courses and not data.warnings


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
        """学期周计算."""
        start = date(2026, 9, 14)
        assert semester_week(date(2026, 9, 14), start) == 1
        assert semester_week(date(2026, 9, 20), start) == 1   # 第 1 周周日
        assert semester_week(date(2026, 9, 21), start) == 2
        assert semester_week(date(2026, 10, 23), start) == 6
        assert semester_week(date(2026, 9, 13), start) == 0   # 学期前


class TestEntriesForWeek:
    """周历聚合."""

    def test_events_by_column_and_sort(self):
        """事件按日期归列, 按 (start, title) 排序."""
        data = ScheduleData(
            courses=[_event(date=date(2026, 10, 23), start=time(14),
                            end=time(15), title="课程")],
            manual=[_event(date=date(2026, 10, 20), start=time(19),
                           end=time(20), title="早课"),
                    _event(date=date(2026, 10, 20), start=time(8),
                           end=time(9), title="讲座")],
        )
        result = entries_for_week(data, date(2026, 10, 19))
        assert [e.title for e in result[2]] == ["讲座", "早课"]  # 周二
        assert [e.title for e in result[5]] == ["课程"]          # 周五
        assert result[2][0].is_manual and not result[5][0].is_manual
        assert result[1] == []

    def test_out_of_week_ignored(self):
        """不在本周的事件忽略."""
        data = ScheduleData(courses=[
            _event(date=date(2026, 10, 18)),  # 上周日
            _event(date=date(2026, 11, 2)),   # 下下周
        ])
        result = entries_for_week(data, date(2026, 10, 19))
        assert all(entries == [] for entries in result.values())


class TestTodoLogic:
    """TODO 排序/状态/变更."""

    def test_sorted_todos(self):
        """未完成在前(有截止早者前, 无截止随后), 完成置底, tid 决胜."""
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
    assert entry.end is None and not entry.is_manual


class TestClampToSemester:
    """学期范围夹取."""

    def test_no_limit_when_unconfigured(self):
        """week_count 为 0 时不限制."""
        monday = date(2026, 10, 19)
        assert clamp_to_semester(monday, date(2026, 9, 14), 0) == monday

    def test_clamps_both_ends(self):
        """早于第 1 周与晚于第 N 周都夹回边界."""
        start = date(2026, 9, 14)
        assert clamp_to_semester(date(2026, 8, 3), start, 4) == start
        assert clamp_to_semester(date(2027, 1, 4), start, 4) == date(2026, 10, 5)

    def test_start_not_monday_aligned(self):
        """start 非周一时对齐到所在周周一."""
        assert clamp_to_semester(date(2026, 8, 3), date(2026, 9, 16), 4) == date(2026, 9, 14)

    def test_inside_unchanged(self):
        """学期内原样返回."""
        assert clamp_to_semester(date(2026, 10, 19), date(2026, 9, 14), 18) == date(2026, 10, 19)
