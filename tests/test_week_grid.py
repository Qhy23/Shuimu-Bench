"""week_grid.py 测试: 纯布局函数与 WeekGridWidget 部件行为."""

from datetime import date, time, timedelta

from PySide6.QtWidgets import QFrame

from shuimu.schedule import DayEntry
from shuimu.week_grid import (
    DAY_MINUTES,
    HEADER_H,
    HOUR_H,
    PPM,
    WeekGridWidget,
    layout_day,
)


def _entry(start, end=None, title="课", location=""):
    """快速构造 DayEntry."""
    return DayEntry(start, end, title, location, is_manual=False)


class TestLayoutDay:
    """纯布局函数: 时间定位, 聚类分道, 边界夹取."""

    def test_single_entry_geometry(self):
        """单条目: y 与高度按起止时刻比例, x 在列内."""
        boxes = layout_day([_entry(time(10), time(11, 30))], 100.0, 200.0)
        assert len(boxes) == 1
        box = boxes[0]
        assert box.x == 102.0  # 100 + GAP
        assert box.y == HEADER_H + (10 * 60 - 8 * 60) * PPM
        assert box.height == 90 * PPM - 2
        assert box.width == 200.0 - 4  # 列宽 - 2*GAP

    def test_no_end_uses_default_duration(self):
        """无结束时刻按默认 95 分钟."""
        box = layout_day([_entry(time(8))], 0.0, 100.0)[0]
        assert box.height == 95 * PPM - 2

    def test_minimum_duration(self):
        """超短条目至少 30 分钟高."""
        box = layout_day([_entry(time(8), time(8, 5))], 0.0, 100.0)[0]
        assert box.height == 30 * PPM - 2

    def test_overlap_splits_lanes(self):
        """重叠两课分两道并排, 各占半宽."""
        boxes = layout_day(
            [_entry(time(10), time(12), "a"), _entry(time(11), time(13), "b")],
            0.0, 100.0)
        assert len(boxes) == 2
        by_title = {box.entry.title: box for box in boxes}
        assert by_title["a"].x + by_title["a"].width <= by_title["b"].x + 1e-6
        assert abs(by_title["a"].width - by_title["b"].width) < 1e-6

    def test_adjacent_not_overlapped(self):
        """首尾相接(结束==下一条开始)不算重叠, 各占整列宽."""
        boxes = layout_day(
            [_entry(time(8), time(9, 35), "a"), _entry(time(9, 35), time(12, 15), "b")],
            0.0, 100.0)
        assert all(box.width == 100.0 - 4 for box in boxes)

    def test_early_entry_clamped_to_top(self):
        """早于 8 点的条目夹到网格顶部."""
        box = layout_day([_entry(time(7), time(9))], 0.0, 100.0)[0]
        assert box.y == HEADER_H

    def test_late_entry_clamped_to_bottom(self):
        """延伸过 22 点的条目夹到网格底部."""
        box = layout_day([_entry(time(21), time(23))], 0.0, 100.0)[0]
        assert box.y + box.height <= HEADER_H + DAY_MINUTES * PPM + 1e-6

    def test_three_way_overlap(self):
        """三课互相重叠分三道."""
        entries = [
            _entry(time(10), time(12), "a"),
            _entry(time(10, 30), time(12), "b"),
            _entry(time(11), time(13), "c"),
        ]
        boxes = layout_day(entries, 0.0, 90.0)
        assert len({round(box.x) for box in boxes}) == 3


class TestWeekGridWidget:
    """部件行为: 卡片生成, 数据接口, 滚动定位."""

    def test_set_week_creates_cards(self, qtbot):
        """set_week 按条目生成 slotcard 子部件."""
        grid = WeekGridWidget()
        qtbot.addWidget(grid)
        grid.resize(800, 800)
        days = {1: [_entry(time(8), time(9, 35), "高数")], 3: []}
        grid.set_week(date(2026, 10, 19), days)
        cards = [child for child in grid.findChildren(QFrame)
                 if child.objectName() == "slotcard"]
        assert len(cards) == 1
        assert grid.entry_titles(1) == ["高数"]
        assert grid.entry_titles(3) == []
        card = cards[0]
        assert card.y() == round(HEADER_H)  # 8:00 课在网格顶
        assert card.height() == round(95 * PPM - 2)

    def test_today_column(self, qtbot):
        """本周返回今天列号, 非本周返回 None."""
        grid = WeekGridWidget()
        qtbot.addWidget(grid)
        monday = date.today() - timedelta(days=date.today().weekday())
        grid.set_week(monday, {})
        assert grid.today_column() == date.today().weekday() + 1
        grid.set_week(monday + timedelta(days=7), {})
        assert grid.today_column() is None
        assert grid.now_offset() == 0

    def test_now_offset_in_range(self, qtbot):
        """本周内 now_offset 落在网格纵向范围内."""
        grid = WeekGridWidget()
        qtbot.addWidget(grid)
        monday = date.today() - timedelta(days=date.today().weekday())
        grid.set_week(monday, {})
        offset = grid.now_offset()
        assert HEADER_H <= offset <= HEADER_H + DAY_MINUTES * PPM

    def test_min_size_covers_full_day(self):
        """最小尺寸覆盖 7 列与 08:00-22:00 全天."""
        grid = WeekGridWidget()
        assert grid.minimumWidth() >= 44 + 7 * 96
        assert grid.minimumHeight() >= HEADER_H + 14 * HOUR_H

    def test_paint_offscreen(self, qtbot):
        """grab() 强制走一遍 paintEvent, 自绘不崩溃且产出非空位图."""
        grid = WeekGridWidget()
        qtbot.addWidget(grid)
        grid.resize(760, 800)
        grid.set_week(date.today() - timedelta(days=date.today().weekday()),
                      {1: [_entry(time(10), time(12), "高数")],
                       2: [_entry(time(19, 20), time(21, 45), "AI 课")]})
        pixmap = grid.grab()
        assert not pixmap.isNull() and pixmap.width() == 760
