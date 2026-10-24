"""artwork.py 测试: 自绘 logo/图标/空状态占位."""

from datetime import date, time

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from shuimu import artwork
from shuimu.controller import Controller
from shuimu.schedule import DayEntry
from shuimu.schedule_view import ScheduleTab
from shuimu.storage import Storage
from shuimu.week_grid import WeekGridWidget


class TestBenchIcon:
    """应用 logo."""

    @pytest.mark.parametrize("size", [16, 32, 48, 256])
    def test_size(self, size):
        """指定边长返回非空方形 pixmap."""
        pixmap = artwork.bench_icon(size)
        assert not pixmap.isNull()
        assert pixmap.width() == size and pixmap.height() == size

    def test_qt_icon(self):
        """QIcon 形式非空."""
        assert not artwork.bench_icon_qt(64).isNull()


class TestKindIcon:
    """课程树节点图标."""

    @pytest.mark.parametrize("kind", ["course", "dir", "file", "link"])
    def test_kinds(self, kind):
        """各类型返回 16px 非空图标."""
        pixmap = artwork.kind_icon(kind)
        assert pixmap.width() == 16 and not pixmap.isNull()

    def test_failed_variant(self):
        """失效变体同样可用."""
        assert not artwork.kind_icon("file", failed=True).isNull()


def test_faint_bench():
    """空状态占位图非空且指定尺寸."""
    pixmap = artwork.faint_bench(110)
    assert not pixmap.isNull() and pixmap.width() == 110


class TestEmptyStates:
    """空状态占位的显隐."""

    def test_week_empty_hint_toggles(self, qtbot):
        """无条目时占位可见, 有条目时隐藏."""
        grid = WeekGridWidget()
        qtbot.addWidget(grid)
        grid.resize(700, 760)
        grid.set_week(date(2026, 10, 19), {wd: [] for wd in range(1, 8)})
        assert not grid._empty.isHidden()
        days = {wd: [] for wd in range(1, 8)}
        days[1] = [DayEntry(time(8), time(9, 35), "课")]
        grid.set_week(date(2026, 10, 19), days)
        assert grid._empty.isHidden()

    def test_todo_empty_hint(self, qtbot, tmp_path, monkeypatch):
        """待办为空时树上有占位提示部件."""
        tpl = tmp_path / "tpl.yaml"
        tpl.write_text("todos: []\n", encoding="utf-8")
        monkeypatch.setattr("shuimu.controller.template_schedule_path",
                            lambda: str(tpl))
        storage = Storage(str(tmp_path / "t.db"))
        ctl = Controller(storage, schedule_path=str(tmp_path / "s.yaml"))
        tab = ScheduleTab(ctl)
        qtbot.addWidget(tab)
        assert isinstance(tab.todo_empty, QWidget)
        assert any(isinstance(child, QLabel)
                   for child in tab.todo_empty.findChildren(QLabel))
        storage.close()
