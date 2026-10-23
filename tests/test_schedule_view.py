"""schedule_view.py GUI 测试: 页签结构, 周历渲染, 待办写回, watcher 重载, 课表导入."""

import os
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QDate
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel

from shuimu import schedule_view
from shuimu.controller import Controller
from shuimu.schedule import load_schedule, week_start
from shuimu.schedule_view import ScheduleTab, TodoDialog
from shuimu.storage import Storage
from shuimu.theme import DANGER


@pytest.fixture()
def sched_env(qtbot, tmp_path, monkeypatch):
    """隔离到 tmp_path 的 Controller + ScheduleTab(模板含一条待办)."""
    tpl_path = tmp_path / "schedule.example.yaml"
    tpl_path.write_text(
        "todos:\n  - {id: 1, title: 模板任务, done: false}\n", encoding="utf-8")
    monkeypatch.setattr("shuimu.controller.template_schedule_path",
                        lambda: str(tpl_path))
    yaml_path = tmp_path / "schedule.yaml"
    storage = Storage(str(tmp_path / "sched.db"))
    ctl = Controller(storage, schedule_path=str(yaml_path))
    tab = ScheduleTab(ctl)
    qtbot.addWidget(tab)
    yield ctl, tab, yaml_path
    storage.close()


def _column_labels(tab, index):
    """某天列内全部 QLabel 文本(含列头与卡片)."""
    return [lbl.text() for lbl in tab.day_columns[index][0].findChildren(QLabel)]


class TestStructure:
    """结构与播种."""

    def test_seeds_and_structure(self, sched_env):
        """构造时播种数据文件; 两段 splitter; 7 天列; 模板待办在列."""
        _ctl, tab, yaml_path = sched_env
        assert yaml_path.exists()
        assert tab.splitter.count() == 2
        assert len(tab.day_columns) == 7
        assert tab.todo_tree.topLevelItemCount() == 1

    def test_today_column_highlighted(self, sched_env):
        """初始显示本周, 恰有一列标记为今天."""
        _ctl, tab, _yaml = sched_env
        names = [col.objectName() for col, _head in tab.day_columns]
        assert names.count("daycol-today") == 1
        assert names.index("daycol-today") == date.today().weekday()

    def test_week_navigation(self, sched_env):
        """翻周后无今天列, 回到今天恢复."""
        _ctl, tab, _yaml = sched_env
        today = date.today()
        assert tab._monday == week_start(today)
        tab._shift_week(1)
        assert tab._monday == week_start(today) + timedelta(days=7)
        assert [c.objectName() for c, _h in tab.day_columns].count(
            "daycol-today") == 0
        tab._go_today()
        assert tab._monday == week_start(today)
        assert [c.objectName() for c, _h in tab.day_columns].count(
            "daycol-today") == 1


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
        monkeypatch.setattr(schedule_view, "_warn",
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
        assert item.foreground(1).color() == QColor(DANGER)


class TestWatcher:
    """文件监听."""

    def test_external_edit_reload(self, sched_env):
        """外部改文件后 _on_file_changed 重载, 新课表条目出现在今天列."""
        _ctl, tab, yaml_path = sched_env
        weekday = date.today().weekday() + 1
        yaml_path.write_text(
            f"timetable:\n"
            f"  - {{weekday: {weekday}, start: '10:00', end: '11:40', title: 外部编辑课}}\n"
            f"todos: []\n", encoding="utf-8")
        tab._on_file_changed(str(yaml_path))
        assert "外部编辑课" in _column_labels(tab, date.today().weekday())

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
        """文件被删除后 _on_dir_changed 重新播种(回到模板初始状态)."""
        _ctl, tab, yaml_path = sched_env
        yaml_path.unlink()
        tab._on_dir_changed("")
        assert yaml_path.exists()
        assert tab.todo_tree.topLevelItemCount() == 1  # 模板里的 模板任务
        assert tab.todo_tree.topLevelItem(0).text(0) == "模板任务"


class TestImportTimetable:
    """从门户导出 .xls 导入课表."""

    def test_import_replaces_timetable(self, sched_env, monkeypatch):
        """确认后导入: timetable 被替换, 留痕与刷新到位."""
        warnings = []
        monkeypatch.setattr(schedule_view, "_warn",
                            lambda _p, title, text: warnings.append((title, text)))
        ctl, tab, yaml_path = sched_env
        fixture = os.path.join(os.path.dirname(__file__), "fixtures",
                               "portal_sample.xls")
        monkeypatch.setattr(
            schedule_view.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (fixture, "")))
        monkeypatch.setattr(
            schedule_view.QMessageBox, "question",
            staticmethod(lambda *a, **k: schedule_view.QMessageBox.StandardButton.Yes))
        tab._import_timetable()
        data = load_schedule(str(yaml_path))
        assert len(data.timetable) == 8
        titles = {slot.title for slot in data.timetable}
        assert "高等数学A" in titles and "AI编程进阶：方法与实践" in titles
        assert any(op["action"] == "import_timetable"
                   for op in ctl.storage.list_operations())
        # 单双周降级提示经 _warn 弹窗(已打桩记录, 不弹真实模态框)
        assert any("单周" in text or "双周" in text
                   for _t, text in warnings)

    def test_import_cancelled(self, sched_env, monkeypatch):
        """确认框选否时不改动文件."""
        _ctl, tab, yaml_path = sched_env
        before = yaml_path.read_text(encoding="utf-8")
        monkeypatch.setattr(schedule_view, "_warn",
                            lambda _p, title, text: None)
        monkeypatch.setattr(
            schedule_view.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: ("whatever.xls", "")))
        monkeypatch.setattr(
            schedule_view.QMessageBox, "question",
            staticmethod(lambda *a, **k: schedule_view.QMessageBox.StandardButton.No))
        tab._import_timetable()
        assert yaml_path.read_text(encoding="utf-8") == before
