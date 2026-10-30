"""应用图形: 纯 QPainter 自绘的 logo/图标/空状态占位图.

不依赖任何资源文件与外部图标库, 全部按主题 token 现画;
小尺寸图标由 256px 母版平滑缩放获得.
"""

import functools

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap

from shuimu.gui import theme

MASTER = 256  # 母版边长


def _pen(color, width: int) -> QPen:
    """圆头画笔."""
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_bench(painter: QPainter, rect: QRectF, color,
                stroke: float) -> None:
    """在 rect 内画简笔长椅: 顶栏/靠背柱/座面/两腿."""
    painter.setPen(_pen(color, stroke))
    x, y = rect.x(), rect.y()
    w, h = rect.width(), rect.height()
    rail_y = y + 0.24 * h
    seat_y = y + 0.56 * h
    left, right = x + 0.14 * w, x + 0.86 * w
    # 靠背顶栏与两根靠背柱
    painter.drawLine(round(left), round(rail_y), round(right), round(rail_y))
    for px in (left, right):
        painter.drawLine(round(px), round(rail_y), round(px), round(seat_y))
    # 座面与两腿
    painter.drawLine(round(left), round(seat_y), round(right), round(seat_y))
    for px in (x + 0.26 * w, x + 0.74 * w):
        painter.drawLine(round(px), round(seat_y), round(px), round(y + 0.9 * h))


def _master_pixmap() -> QPixmap:
    """画 256px logo 母版: 渐变圆角方底 + 白色长椅."""
    pixmap = QPixmap(MASTER, MASTER)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, MASTER, MASTER)
    gradient.setColorAt(0.0, QColor(theme.PRIMARY))
    gradient.setColorAt(1.0, QColor(theme.PRIMARY_LIGHT))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(8, 8, MASTER - 16, MASTER - 16), 56, 56)
    _draw_bench(painter, QRectF(MASTER * 0.24, MASTER * 0.26,
                                MASTER * 0.52, MASTER * 0.5),
                theme.FG_TEXT, MASTER * 0.055)
    painter.end()
    return pixmap


_cache = {"master": None}  # logo 母版惰性缓存


def bench_icon(size: int) -> QPixmap:
    """应用 logo: 指定边长的方形图标."""
    if _cache["master"] is None:
        _cache["master"] = _master_pixmap()
    master = _cache["master"]
    return master.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


def bench_icon_qt(size: int) -> QIcon:
    """QIcon 形式的 logo(窗口图标用)."""
    return QIcon(bench_icon(size))


def faint_bench(size: int) -> QPixmap:
    """空状态占位: 半透明长椅线条(无底色)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor(theme.FG_DIM)
    color.setAlpha(120)
    _draw_bench(painter, QRectF(size * 0.18, size * 0.2,
                                size * 0.64, size * 0.62),
                color, max(2.0, size * 0.05))
    painter.end()
    return pixmap


@functools.lru_cache(maxsize=128)
def kind_icon(kind: str, failed: bool = False, size: int = 16) -> QPixmap:
    """课程树节点小图标: 课程(长椅)/目录(夹)/文件(页)/链接(链环).

    failed 为 True 时(失效资源)整体降灰.
    结果按 (kind, failed, size) 缓存, 供课程树与字号缩放反复取用;
    多主题落地时需加主题维度进缓存键或切换时 cache_clear().
    """
    edge = QColor(theme.FG_DIM if failed else theme.FG_TEXT)
    fill = QColor(theme.BG_PANEL)
    accent = QColor(theme.PRIMARY_LIGHT if not failed else theme.FG_DIM)
    pixmap = QPixmap(size * 2, size * 2)  # 2x 抗锯齿
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    edge_pen = _pen(edge, max(1.5, size * 0.14))
    s = size * 2
    if kind == "course":
        _draw_bench(painter, QRectF(s * 0.2, s * 0.22, s * 0.6, s * 0.6),
                    accent, s * 0.09)
    elif kind == "dir":
        painter.setPen(edge_pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(s * 0.12, s * 0.28, s * 0.76, s * 0.5),
                                4, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(QRectF(s * 0.12, s * 0.18, s * 0.34, s * 0.14))
    elif kind == "link":
        painter.setPen(_pen(accent, max(1.8, size * 0.16)))
        painter.drawEllipse(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56))
        painter.drawLine(round(s * 0.36), round(s * 0.62),
                         round(s * 0.64), round(s * 0.38))
    else:  # file
        painter.setPen(edge_pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(s * 0.24, s * 0.12, s * 0.52, s * 0.76),
                                4, 4)
        painter.setPen(_pen(QColor(theme.FG_DIM), max(1.0, size * 0.1)))
        for i in range(3):
            yy = s * (0.3 + 0.16 * i)
            painter.drawLine(round(s * 0.34), round(yy),
                             round(s * 0.66), round(yy))
    painter.end()
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


def chevron_icon(direction: str, size: int = 20) -> QIcon:
    """chevron 箭头图标(左/右/下), FG_TEXT 色, 圆头折线.

    折线比例参照 thu-info-app 的 IconLeft/IconDown(24x24, strokeWidth 1.5).
    """
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(_pen(theme.FG_TEXT, max(2.0, size * 0.24)))
    s = size * 2.0
    if direction == "left":
        points = [(0.62, 0.26), (0.38, 0.5), (0.62, 0.74)]
    elif direction == "right":
        points = [(0.38, 0.26), (0.62, 0.5), (0.38, 0.74)]
    else:  # down
        points = [(0.30, 0.40), (0.50, 0.62), (0.70, 0.40)]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        painter.drawLine(round(s * x1), round(s * y1), round(s * x2), round(s * y2))
    painter.end()
    return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))
