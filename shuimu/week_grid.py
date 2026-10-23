"""周历时间网格: 课表/事件卡片按起止时刻纵向定位.

背景(小时刻度轴/横线/日分隔线/今天列底色/当前时刻线)由 paintEvent 自绘;
卡片是绝对定位的 QFrame 子部件(沿用主题 QSS 的 slotcard 样式).
重叠条目自动聚类分道并排. 时间网格几何计算是纯函数, 便于单测.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from . import theme
from .schedule import DayEntry, week_start

AXIS_W = 44            # 左侧时刻轴宽(px)
HEADER_H = 30          # 顶部日头条高(px)
HOUR_H = 52            # 每小时像素高
PPM = HOUR_H / 60.0    # 每分钟像素
DAY_START = time(8, 0)
DAY_MINUTES = 14 * 60  # 08:00-22:00
DEFAULT_MIN = 95       # 无结束时刻时的默认时长(分钟, 一个大节)
MIN_CARD_MIN = 30      # 卡片最短时长(分钟)
COL_MIN_W = 96         # 单列最小宽
DAY_COUNT = 7
GAP = 2

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _end_minutes(entry: DayEntry) -> int:
    return _minutes(entry.end) if entry.end else _minutes(entry.start) + DEFAULT_MIN


@dataclass
class CardBox:
    """一张卡片的几何(浮点, 挂到部件时取整)."""

    entry: DayEntry
    x: float
    y: float
    width: float
    height: float


def _assign_lanes(entries: list) -> list:
    """同簇内条目贪心分道: 放进"最后结束不晚于本条开始"的已有道."""
    lanes = []
    for entry in sorted(entries, key=lambda e: (e.start, e.title)):
        for lane in lanes:
            if _end_minutes(lane[-1]) <= _minutes(entry.start):
                lane.append(entry)
                break
        else:
            lanes.append([entry])
    return lanes


def _layout_cluster(items: list, x: float, col_w: float) -> list:
    """簇内分道并排, 返回 CardBox 列表(早晚超界夹到网格边界)."""
    lanes = _assign_lanes(items)
    lane_w = col_w / len(lanes)
    grid_bottom = HEADER_H + DAY_MINUTES * PPM
    boxes = []
    for lane_index, lane in enumerate(lanes):
        for entry in lane:
            start_m = _minutes(entry.start)
            top = max(HEADER_H + (start_m - _minutes(DAY_START)) * PPM,
                      HEADER_H)
            height = (max(_end_minutes(entry) - start_m, MIN_CARD_MIN)
                      * PPM) - GAP
            bottom = min(top + height, grid_bottom)
            boxes.append(CardBox(entry, x + lane_index * lane_w + GAP, top,
                                 lane_w - 2 * GAP, max(bottom - top, 12.0)))
    return boxes


def layout_day(entries: list, x: float, col_w: float) -> list:
    """把一天的条目布局成 CardBox 列表: 重叠聚类, 簇内分道并排.

    早于 8 点的条目夹到网格顶部, 晚于 22 点的夹到底部.
    """
    ordered = sorted(entries, key=lambda e: (e.start, e.title))
    clusters = []  # 每簇: {"items": [...], "end": 簇内最晚结束分钟}
    for entry in ordered:
        end = _end_minutes(entry)
        if clusters and _minutes(entry.start) < clusters[-1]["end"]:
            clusters[-1]["items"].append(entry)
            clusters[-1]["end"] = max(clusters[-1]["end"], end)
        else:
            clusters.append({"items": [entry], "end": end})
    boxes = []
    for cluster in clusters:
        boxes.extend(_layout_cluster(cluster["items"], x, col_w))
    return boxes


class WeekGridWidget(QWidget):
    """自绘时间网格 + 绝对定位卡片. 数据经 set_week 全量更新."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._monday = week_start(date.today())
        self._days = {wd: [] for wd in range(1, DAY_COUNT + 1)}
        self._cards = []  # [(QFrame, DayEntry, weekday)]
        self.setMinimumSize(AXIS_W + DAY_COUNT * COL_MIN_W,
                            int(HEADER_H + DAY_MINUTES * PPM))

    # ---- 数据接口 ----

    def set_week(self, monday: date, days: dict) -> None:
        """设置显示周与各天条目, 重建卡片."""
        self._monday = monday
        self._days = days
        for card, _entry, _weekday in self._cards:
            card.deleteLater()
        self._cards = []
        for weekday, entries in days.items():
            for entry in entries:
                card = self._make_card(entry)
                self._cards.append((card, entry, weekday))
        for card, _entry, _weekday in self._cards:
            card.show()
        self._relayout()
        self.update()

    def today_column(self):
        """今天所在列(1-7); 显示的不是本周时返回 None."""
        today = date.today()
        if week_start(today) != self._monday:
            return None
        return today.weekday() + 1

    def entry_titles(self, weekday: int) -> list:
        """某天(1-7)的条目标题, 供测试与外部核对."""
        return [entry.title for entry in self._days.get(weekday, [])]

    def now_offset(self) -> int:
        """当前时刻线的 y 坐标(供滚动定位); 非本周返回 0."""
        if self.today_column() is None:
            return 0
        now_m = datetime.now().hour * 60 + datetime.now().minute
        offset = HEADER_H + (now_m - _minutes(DAY_START)) * PPM
        return int(max(HEADER_H, min(offset, HEADER_H + DAY_MINUTES * PPM)))

    # ---- 卡片 ----

    def _make_card(self, entry: DayEntry) -> QFrame:
        """一张条目卡片: 时段/标题/地点三行, 悬停提示完整信息."""
        card = QFrame(self)
        card.setObjectName("slotcard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(0)
        end_text = entry.end.strftime("%H:%M") if entry.end else ""
        time_label = QLabel(f"{entry.start.strftime('%H:%M')}-{end_text}", card)
        title_label = QLabel(entry.title, card)
        title_label.setObjectName("cardtitle")
        title_label.setWordWrap(True)
        layout.addWidget(time_label)
        layout.addWidget(title_label)
        if entry.location:
            location_label = QLabel(entry.location, card)
            location_label.setWordWrap(True)
            layout.addWidget(location_label)
        card.setToolTip(f"{entry.title}\n{time_label.text()}"
                        + (f"\n{entry.location}" if entry.location else ""))
        return card

    def _relayout(self) -> None:
        """按当前宽度重排全部卡片几何."""
        col_w = max((self.width() - AXIS_W) / DAY_COUNT, COL_MIN_W)
        for weekday in range(1, DAY_COUNT + 1):
            base_x = AXIS_W + (weekday - 1) * col_w
            for box in layout_day(self._days.get(weekday, []), base_x, col_w):
                for card, entry, card_weekday in self._cards:
                    if card_weekday == weekday and entry is box.entry:
                        card.setGeometry(int(box.x), int(box.y),
                                         int(box.width), int(box.height))
                        break

    def resizeEvent(self, _event) -> None:
        """尺寸变化时重排卡片."""
        self._relayout()

    # ---- 自绘 ----

    def _draw_axis(self, painter) -> None:
        """左侧小时刻度文字与横向网格线."""
        axis_font = QFont(self.font())
        axis_font.setPixelSize(10)
        painter.setFont(axis_font)
        for i in range(DAY_MINUTES // 60 + 1):
            y = HEADER_H + i * HOUR_H
            painter.setPen(QColor(theme.BORDER))
            painter.drawLine(AXIS_W, int(y), self.width(), int(y))
            painter.setPen(QColor(theme.FG_DIM))
            painter.drawText(QRectF(0, y - 12, AXIS_W - 6, 12),
                             Qt.AlignmentFlag.AlignRight
                             | Qt.AlignmentFlag.AlignVCenter,
                             f"{8 + i:02d}:00")

    def _draw_headers(self, painter, col_w: float, today_column) -> None:
        """日分隔竖线与日头文字(今天加粗高亮)."""
        header_font = QFont(self.font())
        for weekday in range(1, DAY_COUNT + 1):
            x = AXIS_W + (weekday - 1) * col_w
            painter.setPen(QColor(theme.BORDER))
            painter.drawLine(int(x), 0, int(x), self.height())
            day = self._monday + timedelta(days=weekday - 1)
            is_today = weekday == today_column
            header_font.setBold(is_today)
            painter.setFont(header_font)
            painter.setPen(QColor(theme.PRIMARY if is_today else theme.FG_DIM))
            painter.drawText(QRectF(x, 0, col_w, HEADER_H),
                             Qt.AlignmentFlag.AlignCenter,
                             f"{WEEKDAY_NAMES[weekday - 1]} {day.strftime('%m/%d')}")
        right = AXIS_W + int(DAY_COUNT * col_w)
        painter.setPen(QColor(theme.BORDER))
        painter.drawLine(right, 0, right, self.height())

    def _draw_now_line(self, painter, col_w: float) -> None:
        """当前时刻的红色横线与左端圆点."""
        now = datetime.now()
        minutes_now = now.hour * 60 + now.minute
        y = HEADER_H + (minutes_now - _minutes(DAY_START)) * PPM
        bottom = min(HEADER_H + DAY_MINUTES * PPM, self.height())
        if HEADER_H <= y <= bottom:
            pen = QPen(QColor(theme.DANGER))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(AXIS_W, int(y),
                             AXIS_W + int(DAY_COUNT * col_w), int(y))
            painter.setBrush(QColor(theme.DANGER))
            painter.drawEllipse(int(AXIS_W - 3), int(y - 3), 6, 6)

    def paintEvent(self, _event) -> None:
        """画时刻轴/小时横线/日分隔线/日头/今天列底色/当前时刻线."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        col_w = max((self.width() - AXIS_W) / DAY_COUNT, COL_MIN_W)
        grid_bottom = HEADER_H + DAY_MINUTES * PPM
        today_column = self.today_column()

        painter.fillRect(self.rect(), QColor(theme.BG_BASE))
        if today_column:
            tint = QColor(theme.PRIMARY)
            tint.setAlpha(36)
            x = AXIS_W + (today_column - 1) * col_w
            painter.fillRect(QRectF(x, HEADER_H, col_w,
                                    min(grid_bottom, self.height()) - HEADER_H),
                             tint)
        self._draw_axis(painter)
        self._draw_headers(painter, col_w, today_column)
        if today_column:
            self._draw_now_line(painter, col_w)
        painter.end()
