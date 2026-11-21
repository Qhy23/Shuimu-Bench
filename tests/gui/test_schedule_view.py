"""schedule_view.py GUI 测试: 页签结构, 周历渲染, 待办写回, watcher 重载, 课表导入."""

import os
from copy import deepcopy
from datetime import date, time, timedelta

import pytest
from PySide6.QtCore import QDate, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton, QWidget

from shuimu.gui import schedule_view, theme
from shuimu.core.config import DEFAULTS
from shuimu.core.controller import Controller
from shuimu.core.schedule import Event, load_schedule, week_start
from shuimu.gui.schedule_view import (
    ManualEntryDialog,
    ScheduleTab,
    TodoDialog,
    WeekPickerPopup,
)
from shuimu.core.storage import Storage


@pytest.fixture()
def sched_env(qtbot, tmp_path):
    """隔离到 tmp_path 的 Controller + ScheduleTab(预置一条待办)."""
    yaml_path = tmp_path / "schedule.yaml"
    yaml_path.write_text(
        "todos:\n  - {id: 1, title: 模板任务, done: false}\n", encoding="utf-8")
    storage = Storage(str(tmp_path / "sched.db"))
    ctl = Controller(storage, schedule_path=str(yaml_path))
    tab = ScheduleTab(ctl)
    qtbot.addWidget(tab)
    yield ctl, tab, yaml_path
    storage.close()






class TestStructure:
    """结构与播种."""

    def test_seeds_and_structure(self, sched_env):
        """构造时播种数据文件; 两段 splitter; 周历网格就绪; 模板待办在列."""
        _ctl, tab, yaml_path = sched_env
        assert yaml_path.exists()
        assert tab.splitter.count() == 2
        assert tab.week_grid is not None
        assert tab.todo_tree.topLevelItemCount() == 1

    def test_today_column_highlighted(self, sched_env):
        """初始显示本周, 网格标记今天所在列."""
        _ctl, tab, _yaml = sched_env
        assert tab.week_grid.today_column() == date.today().weekday() + 1

    def test_week_navigation(self, sched_env):
        """翻周后今天列消失, 回到今天恢复."""
        _ctl, tab, _yaml = sched_env
        today = date.today()
        assert tab._monday == week_start(today)
        tab._shift_week(1)
        assert tab._monday == week_start(today) + timedelta(days=7)
        assert tab.week_grid.today_column() is None
        tab._go_today()
        assert tab._monday == week_start(today)
        assert tab.week_grid.today_column() == today.weekday() + 1


class TestTodoDialog:
    """新建待办对话框."""

    def test_values(self, qtbot):
        """默认无截止不关联; 勾选后返回日期与课程."""
        dialog = TodoDialog(["微积分A(1)"])
        qtbot.addWidget(dialog)
        assert dialog.values() == ("", None, "")
        dialog.title_edit.setText("  作业 ")
        dialog.due_check.setChecked(True)
        dialog.due_edit.setDate(QDate(2026, 10, 26))
        dialog.course_box.setCurrentIndex(1)
        assert dialog.values() == ("作业", date(2026, 10, 26), "微积分A(1)")

    def test_blank_title_rejected(self, qtbot, monkeypatch):
        """空标题弹警告且对话框不被接受."""
        warnings = []
        monkeypatch.setattr(schedule_view, "warn",
                            lambda _p, title, text: warnings.append((title, text)))
        dialog = TodoDialog([])
        qtbot.addWidget(dialog)
        dialog._accept_if_valid()
        assert len(warnings) == 1
        assert dialog.result() == 0  # 未 accept


class TestTodoActions:
    """待办新建与勾选写回."""

    def test_add_todo_persists(self, sched_env):
        """_mutate 走 controller: 落盘, 树更新, 留痕."""
        ctl, tab, yaml_path = sched_env
        tab._mutate(lambda: ctl.add_todo("新任务", None, ""))
        data = load_schedule(str(yaml_path))
        assert [t.title for t in data.todos] == ["模板任务", "新任务"]
        assert tab.todo_tree.topLevelItemCount() == 2
        assert any(op["action"] == "add_todo"
                   for op in ctl.storage.list_operations())

    def test_toggle_writes_back_without_signal_loop(self, sched_env):
        """勾选写回 done 且只留一次痕(渲染期 blockSignals 不误触发)."""
        ctl, tab, yaml_path = sched_env
        item = tab.todo_tree.topLevelItem(0)
        tab._on_item_changed(item, 0)
        toggles = [op for op in ctl.storage.list_operations()
                   if op["action"] == "toggle_todo"]
        assert len(toggles) == 1  # 若信号回路会翻两次/留两痕
        assert load_schedule(str(yaml_path)).todos[0].done is True
        done_item = tab.todo_tree.topLevelItem(tab.todo_tree.topLevelItemCount() - 1)
        assert done_item.font(0).strikeOut()

    def test_overdue_highlight(self, sched_env):
        """逾期待办截止列红色并标注."""
        _ctl, tab, yaml_path = sched_env
        yesterday = date.today() - timedelta(days=1)
        yaml_path.write_text(
            f"todos:\n  - {{id: 1, title: 逾期任务, due: {yesterday.isoformat()}, done: false}}\n",
            encoding="utf-8")
        tab._render_all()
        item = tab.todo_tree.topLevelItem(0)
        assert "逾期" in item.text(1)
        assert item.foreground(1).color() == QColor(theme.tokens().danger)


class TestWatcher:
    """文件监听."""

    def test_external_edit_reload(self, sched_env):
        """外部改文件后 _on_file_changed 重载, 新课表条目出现在今天列."""
        _ctl, tab, yaml_path = sched_env
        weekday = date.today().weekday() + 1
        today = date.today()
        yaml_path.write_text(
            f"courses:\n"
            f"  - {{date: {today.isoformat()}, start: '10:00', end: '11:40', title: 外部编辑课}}\n"
            f"todos: []\n", encoding="utf-8")
        tab._on_file_changed(str(yaml_path))
        assert "外部编辑课" in tab.week_grid.entry_titles(weekday)

    def test_self_write_suppressed_once(self, sched_env, monkeypatch):
        """自写标志消费一次且不刷新; 之后的外部变化恢复刷新."""
        _ctl, tab, yaml_path = sched_env
        calls = []
        monkeypatch.setattr(tab, "_render_all", lambda: calls.append(1))
        tab._self_write = True
        tab._on_file_changed(str(yaml_path))
        assert not calls and tab._self_write is False
        tab._on_file_changed(str(yaml_path))
        assert len(calls) == 1

    def test_dir_changed_reseeds(self, sched_env):
        """文件被删除后 _on_dir_changed 重新播种(空骨架)."""
        _ctl, tab, yaml_path = sched_env
        yaml_path.unlink()
        tab._on_dir_changed("")
        assert yaml_path.exists()
        assert "水木工作台日程数据" in yaml_path.read_text(encoding="utf-8")
        assert tab.todo_tree.topLevelItemCount() == 0


class TestImportCourses:
    """从个人校历 html 导入课程."""

    def test_import_replaces_courses(self, sched_env, monkeypatch):
        """确认后导入: courses 被替换(联排合并), 留痕与刷新到位."""
        warnings = []
        monkeypatch.setattr(schedule_view, "warn",
                            lambda _p, title, text: warnings.append((title, text)))
        ctl, tab, yaml_path = sched_env
        fixture = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                               "portal_calendar_sample.html")
        monkeypatch.setattr(
            schedule_view.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (fixture, "")))
        monkeypatch.setattr(
            schedule_view.QMessageBox, "question",
            staticmethod(lambda *a, **k: schedule_view.QMessageBox.StandardButton.Yes))
        tab._import_courses()
        data = load_schedule(str(yaml_path))
        assert len(data.courses) == 3  # 4 行联排合并 1 处
        math_class = [e for e in data.courses if e.title == "高等数学A(1)"][0]
        assert math_class.end == time(11, 25)
        assert any(op["action"] == "import_courses"
                   for op in ctl.storage.list_operations())
        assert not warnings  # fixture 干净, 不弹提示

    def test_import_cancelled(self, sched_env, monkeypatch):
        """确认框选否时不改动文件."""
        _ctl, tab, yaml_path = sched_env
        before = yaml_path.read_text(encoding="utf-8")
        monkeypatch.setattr(schedule_view, "warn",
                            lambda _p, title, text: None)
        monkeypatch.setattr(
            schedule_view.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: ("whatever.html", "")))
        monkeypatch.setattr(
            schedule_view.QMessageBox, "question",
            staticmethod(lambda *a, **k: schedule_view.QMessageBox.StandardButton.No))
        tab._import_courses()
        assert yaml_path.read_text(encoding="utf-8") == before


class TestSemesterRange:
    """学期周次显示与翻周夹取."""

    @pytest.fixture()
    def sem_env(self, qtbot, tmp_path):
        """配置了学期起点与总周数的隔离环境(第 1 周 = 2026-10-19, 共 4 周)."""
        cfg = deepcopy(DEFAULTS)
        cfg["schedule"]["semester_start"] = "2026-10-19"
        cfg["schedule"]["week_count"] = 4
        storage = Storage(str(tmp_path / "sem.db"))
        ctl = Controller(storage, config=cfg,
                         schedule_path=str(tmp_path / "s.yaml"))
        tab = ScheduleTab(ctl)
        qtbot.addWidget(tab)
        yield tab
        storage.close()

    def test_label_week_number(self, sem_env):
        """学期内显示第 N 周, 从 1 开始."""
        sem_env._monday = date(2026, 10, 19)
        sem_env._render_all()
        assert "第 1 周" in sem_env.week_btn.text()
        sem_env._monday = date(2026, 11, 2)
        sem_env._render_all()
        assert "第 3 周" in sem_env.week_btn.text()

    def test_label_vacation_out_of_range(self, sem_env):
        """学期前与学期后显示假期."""
        sem_env._monday = date(2026, 10, 12)  # 开学前
        sem_env._render_all()
        assert "假期" in sem_env.week_btn.text()
        sem_env._monday = date(2026, 11, 23)  # 第 6 周, 超出 4 周
        sem_env._render_all()
        assert "假期" in sem_env.week_btn.text()

    def test_shift_clamped_to_semester(self, sem_env):
        """翻周被夹在第 1 周与第 4 周之间."""
        sem_env._monday = date(2026, 10, 19)
        sem_env._shift_week(-1)
        assert sem_env._monday == date(2026, 10, 19)  # 已在第 1 周
        sem_env._shift_week(9)
        assert sem_env._monday == date(2026, 11, 9)   # 夹到第 4 周周一


class TestManualEntryDialog:
    """手动添加/修改对话框."""

    def _dialog(self, qtbot, series_mode=False, initial=None):
        dialog = ManualEntryDialog(date(2026, 9, 14), 18,
                                   series_mode=series_mode, initial=initial)
        qtbot.addWidget(dialog)
        return dialog

    def test_date_mode_build(self, qtbot):
        """日期模式返回单次事件."""
        dialog = self._dialog(qtbot)
        dialog.date_mode_btn.setChecked(True)
        dialog.title_edit.setText("组会")
        dialog.location_edit.setText("实验室")
        dialog.date_edit.setDate(QDate(2026, 10, 28))
        dialog.date_start_edit.setTime(QTime(14, 0))
        dialog.date_end_edit.setTime(QTime(16, 0))
        events = dialog.build_events()
        assert len(events) == 1
        assert (events[0].date, events[0].start, events[0].end) == (
            date(2026, 10, 28), time(14), time(16))
        assert events[0].title == "组会" and events[0].location == "实验室"

    def test_week_mode_expands(self, qtbot):
        """周数模式按学期起点展开为逐次事件."""
        dialog = self._dialog(qtbot)
        dialog.title_edit.setText("答疑")
        dialog.weekday_box.setCurrentIndex(2)  # 周三
        dialog.session_box.setCurrentIndex(0)  # 第 1 节 08:00-09:35
        for week, chip in enumerate(dialog.week_buttons, 1):
            chip.setChecked(week in (1, 3))    # 第 1 与第 3 周
        events = dialog.build_events()
        assert [(e.date, e.start, e.end) for e in events] == [
            (date(2026, 9, 16), time(8), time(9, 35)),
            (date(2026, 9, 30), time(8), time(9, 35)),
        ]

    def test_week_shortcuts(self, qtbot):
        """单周快捷选择只勾奇数周."""
        dialog = self._dialog(qtbot)
        dialog._set_weeks(lambda w: w % 2 == 1)
        assert dialog._checked_weeks() == [1, 3, 5, 7, 9, 11, 13, 15, 17]

    def test_session_preset_fills_times(self, qtbot):
        """大节预设填充起止时间."""
        dialog = self._dialog(qtbot)
        dialog.session_box.setCurrentIndex(5)  # 第 6 节
        assert dialog.week_start_edit.time().toPython() == time(19, 20)
        assert dialog.week_end_edit.time().toPython() == time(21, 45)

    def test_validations(self, qtbot, monkeypatch):
        """空标题/结束不晚于开始/未选周次均拒绝."""
        warnings = []
        monkeypatch.setattr(schedule_view, "warn",
                            lambda _p, title, text: warnings.append(title))
        dialog = self._dialog(qtbot)
        dialog._accept_if_valid()
        assert len(warnings) == 1  # 空标题
        dialog.title_edit.setText("课")
        dialog.week_end_edit.setTime(QTime(7, 0))  # 早于开始
        dialog._accept_if_valid()
        assert len(warnings) == 2
        dialog.week_end_edit.setTime(QTime(9, 35))
        dialog._set_weeks(lambda w: False)  # 未选周次
        dialog._accept_if_valid()
        assert len(warnings) == 3
        assert dialog.result() == 0

    def test_prefill_and_series_mode(self, qtbot):
        """修改预填进日期模式; 系列模式日期不可改."""
        initial = {"title": "旧题", "location": "旧地",
                   "date": date(2026, 10, 26), "start": time(10),
                   "end": time(12)}
        dialog = self._dialog(qtbot, initial=initial)
        assert dialog.date_mode_btn.isChecked()
        assert dialog.date_edit.date().toPython() == date(2026, 10, 26)
        series = self._dialog(qtbot, series_mode=True, initial=initial)
        assert not series.date_edit.isEnabled()
        series.title_edit.setText("新题")
        event = series.build_events()[0]
        assert event.title == "新题" and event.start == time(10)


class TestCardActions:
    """卡片右键对应的增删改处理."""

    def test_add_manual_via_tab(self, sched_env, monkeypatch):
        """_add_manual 经对话框写回 manual 段."""
        ctl, tab, yaml_path = sched_env

        class FakeDialog:
            """替身: 直接返回一次晚自习事件."""

            def __init__(self, *args, **kwargs):
                """忽略预填参数."""

            def exec(self):
                """视为用户点了确定."""
                return schedule_view.QDialog.DialogCode.Accepted

            def build_events(self):
                """返回固定事件."""
                return [Event(date(2026, 10, 26), time(19), time(21),
                              "晚自习", "图书馆", 0)]

        monkeypatch.setattr(schedule_view, "ManualEntryDialog", FakeDialog)
        tab._add_manual()
        data = load_schedule(str(yaml_path))
        assert len(data.manual) == 1 and data.manual[0].title == "晚自习"
        assert any(op["action"] == "add_manual"
                   for op in ctl.storage.list_operations())

    def test_remove_entry_with_confirm(self, sched_env, monkeypatch):
        """确认后删除本次."""
        _ctl, tab, yaml_path = sched_env
        yaml_path.write_text(
            "manual:\n"
            "  - {date: '2026-10-26', start: '19:00', end: '21:00', title: 晚自习}\n"
            "todos: []\n", encoding="utf-8")
        tab._render_all()
        origin = load_schedule(str(yaml_path)).manual[0]
        monkeypatch.setattr(
            schedule_view.QMessageBox, "question",
            staticmethod(lambda *a, **k: schedule_view.QMessageBox.StandardButton.Yes))
        tab._remove_entry("manual", origin, False)
        assert not load_schedule(str(yaml_path)).manual

    def test_edit_entry_updates_in_place(self, sched_env, monkeypatch):
        """修改本次: 预填对话框返回新事件, 写回替换."""
        _ctl, tab, yaml_path = sched_env
        yaml_path.write_text(
            "manual:\n"
            "  - {date: '2026-10-26', start: '19:00', end: '21:00', title: 晚自习}\n"
            "todos: []\n", encoding="utf-8")
        tab._render_all()
        origin = load_schedule(str(yaml_path)).manual[0]

        class FakeDialog:
            """替身: 返回改期后的新事件."""

            def __init__(self, *args, **kwargs):
                """忽略预填参数."""

            def exec(self):
                """视为用户点了确定."""
                return schedule_view.QDialog.DialogCode.Accepted

            def build_events(self):
                """返回固定事件."""
                return [Event(date(2026, 10, 26), time(14), time(16),
                              "改期自习", "", 0)]

        monkeypatch.setattr(schedule_view, "ManualEntryDialog", FakeDialog)
        tab._edit_entry("manual", origin, False)
        data = load_schedule(str(yaml_path))
        assert data.manual[0].title == "改期自习"
        assert data.manual[0].start == time(14)


class TestWeekNavigationControls:
    """箭头翻周与周次快速跳转."""

    def test_arrow_buttons_have_icons(self, sched_env):
        """左右按钮为图标按钮(chevron), 无文字."""
        _ctl, tab, _yaml = sched_env
        assert tab.prev_btn.text() == "" and tab.next_btn.text() == ""
        assert not tab.prev_btn.icon().isNull()
        assert not tab.next_btn.icon().isNull()
        assert tab.prev_btn.toolTip() == "上一周"

    def test_week_picker_popup(self, qtbot):
        """面板按钮数正确, 选中周主样式, 点击发出周次信号."""
        popup = WeekPickerPopup(18, 6, 3)
        qtbot.addWidget(popup)
        buttons = popup.findChildren(QPushButton)
        assert len(buttons) == 18
        chosen = []
        popup.weekChosen.connect(chosen.append)
        current = next(b for b in buttons if b.text() == "6")
        assert current.property("primary") is True
        today_btn = next(b for b in buttons if b.text() == "3")
        assert today_btn.property("today") is True
        next(b for b in buttons if b.text() == "9").click()
        assert chosen == [9]

    def test_goto_week(self, sched_env):
        """_goto_week 跳到指定周周一."""
        _ctl, tab, _yaml = sched_env
        start = tab.controller.semester_start()
        assert start is not None  # 测试配置固定学期
        tab._goto_week(3)
        assert tab._monday == start + timedelta(weeks=2)

    def test_week_button_text_has_caret(self, sched_env):
        """周标签按钮文本含下箭头标记."""
        _ctl, tab, _yaml = sched_env
        assert "▾" in tab.week_btn.text()
        assert "周" in tab.week_btn.text() or "假期" in tab.week_btn.text()


class TestZoomDetails:
    """缩放细节: 面板按钮与树图标随字号."""

    def test_picker_buttons_grow_with_font(self, qtbot):
        """20px 字号下面板按钮放大, 数字有容身之处."""
        host = QWidget()
        qtbot.addWidget(host)
        theme.apply_theme(host, 13)  # 隔离此前测试遗留的字号
        popup_small = WeekPickerPopup(4, 1, 1)
        qtbot.addWidget(popup_small)
        small = popup_small.findChildren(QPushButton)[0].width()
        theme.apply_theme(host, 20)
        popup_big = WeekPickerPopup(4, 1, 1)
        qtbot.addWidget(popup_big)
        big = popup_big.findChildren(QPushButton)[0].width()
        assert big > small >= 34
        theme.apply_theme(host, 13)
