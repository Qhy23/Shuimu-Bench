"""应用图形: 纯 QPainter 自绘的 logo/图标/空状态占位图.

不依赖任何资源文件与外部图标库, 全部按当前主题 token 现画;
小尺寸图标由 256px 母版或 2x 超采样平滑缩放获得. 主题切换时
clear_cache() 使按色缓存的图标重建.
"""

import functools
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)

from shuimu.gui import theme

MASTER = 256  # 母版边长


def _pen(color, width: int) -> QPen:
    """圆头画笔."""
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def clear_cache() -> None:
    """主题切换后清空全部图标缓存(颜色随之重建)."""
    _cache["master"] = None
    kind_icon.cache_clear()


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
    tk = theme.tokens()
    pixmap = QPixmap(MASTER, MASTER)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, MASTER, MASTER)
    gradient.setColorAt(0.0, QColor(tk.accent))
    gradient.setColorAt(1.0, QColor(tk.accent_hover))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(8, 8, MASTER - 16, MASTER - 16), 56, 56)
    _draw_bench(painter, QRectF(MASTER * 0.24, MASTER * 0.26,
                                MASTER * 0.52, MASTER * 0.5),
                tk.accent_fg, MASTER * 0.055)
    painter.end()
    return pixmap


_cache = {"master": None}  # logo 母版惰性缓存

# 主题切换时清缓存, 使按色缓存的图标重建(注册替代反向导入, 避免环)
theme.on_theme_changed(clear_cache)


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
    color = QColor(theme.tokens().fg_dim)
    color.setAlpha(120)
    _draw_bench(painter, QRectF(size * 0.18, size * 0.2,
                                size * 0.64, size * 0.62),
                color, max(2.0, size * 0.05))
    painter.end()
    return pixmap


@functools.lru_cache(maxsize=256)
def kind_icon(kind: str, failed: bool = False, size: int = 16,
              color: str = None) -> QPixmap:
    """课程树节点小图标: 课程(长椅)/目录(夹)/文件(页)/链接(链环).

    failed 为 True 时(失效资源)整体降灰; color 可覆盖课程长椅的
    强调色(课程稳定配色), 结果按 (kind, failed, size, color) 缓存,
    供课程树与字号缩放反复取用; 主题切换经 clear_cache() 重建.
    """
    tk = theme.tokens()
    edge = QColor(tk.fg_dim if failed else tk.fg)
    fill = QColor(tk.panel)
    accent = QColor(color or (tk.accent_hover if not failed else tk.fg_dim))
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
        painter.setPen(_pen(QColor(tk.fg_dim), max(1.0, size * 0.1)))
        for i in range(3):
            yy = s * (0.3 + 0.16 * i)
            painter.drawLine(round(s * 0.34), round(yy),
                             round(s * 0.66), round(yy))
    painter.end()
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


def chevron_icon(direction: str, size: int = 20) -> QIcon:
    """chevron 箭头图标(左/右/下), 主文字色, 圆头折线.

    折线比例参照 thu-info-app 的 IconLeft/IconDown(24x24, strokeWidth 1.5).
    """
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(_pen(theme.tokens().fg, max(2.0, size * 0.24)))
    s = size * 2.0
    if direction == "left":
        points = [(0.62, 0.26), (0.38, 0.5), (0.62, 0.74)]
    elif direction == "right":
        points = [(0.38, 0.26), (0.62, 0.5), (0.38, 0.74)]
    else:  # down
        points = [(0.30, 0.40), (0.50, 0.62), (0.70, 0.40)]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        painter.drawLine(round(s * x1), round(s * y1), round(s * x2),
                         round(s * y2))
    painter.end()
    return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))


def log_icon(size: int = 16) -> QIcon:
    """日志(操作留痕)按钮图标: 圆角矩形描边 + 三横线, 次要文字色单色."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size * 2.0
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(theme.tokens().fg_dim, max(1.5, size * 0.12)))
    painter.drawRoundedRect(QRectF(s * 0.18, s * 0.14, s * 0.64, s * 0.72),
                            s * 0.08, s * 0.08)
    for i in range(3):
        yy = s * (0.32 + 0.18 * i)
        painter.drawLine(round(s * 0.3), round(yy), round(s * 0.7), round(yy))
    painter.end()
    return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))


def window_icon(kind: str, size: int = 12) -> QIcon:
    """窗口控制图标: min(横线)/max(方框)/restore(重叠双框)/close(叉).

    主文字色细线, 标题栏专用, hover 高亮由 QSS 底色承担.
    """
    tk = theme.tokens()
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size * 2.0
    stroke = max(1.6, size * 0.16)
    painter.setPen(_pen(tk.fg, stroke))
    if kind == "min":
        painter.drawLine(round(s * 0.22), round(s * 0.5),
                         round(s * 0.78), round(s * 0.5))
    elif kind == "max":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(s * 0.24, s * 0.24, s * 0.52, s * 0.52))
    elif kind == "restore":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(s * 0.18, s * 0.3, s * 0.44, s * 0.44))
        painter.drawRect(QRectF(s * 0.4, s * 0.18, s * 0.42, s * 0.42))
    else:  # close
        painter.drawLine(round(s * 0.28), round(s * 0.28),
                         round(s * 0.72), round(s * 0.72))
        painter.drawLine(round(s * 0.72), round(s * 0.28),
                         round(s * 0.28), round(s * 0.72))
    painter.end()
    return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))


def settings_icon(size: int = 16) -> QIcon:
    """设置(齿轮)图标: 圆环 + 六辐条 + 中心点."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size * 2.0
    cx = cy = s / 2
    painter.setPen(_pen(theme.tokens().fg_dim, max(1.5, size * 0.12)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(s * 0.26, s * 0.26, s * 0.48, s * 0.48))
    for i in range(6):
        angle = math.pi / 3 * i
        x1 = cx + s * 0.2 * math.cos(angle)
        y1 = cy + s * 0.2 * math.sin(angle)
        x2 = cx + s * 0.36 * math.cos(angle)
        y2 = cy + s * 0.36 * math.sin(angle)
        painter.drawLine(round(x1), round(y1), round(x2), round(y2))
    painter.setBrush(QColor(theme.tokens().fg_dim))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(cx - s * 0.05, cy - s * 0.05,
                               s * 0.1, s * 0.1))
    painter.end()
    return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))


def about_icon(size: int = 14) -> QIcon:
    """关于图标: 圆环 + i(点与竖线)."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size * 2.0
    painter.setPen(_pen(theme.tokens().fg_dim, max(1.5, size * 0.12)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.68))
    painter.setPen(_pen(theme.tokens().fg_dim, max(1.6, size * 0.13)))
    painter.drawLine(round(s * 0.5), round(s * 0.44), round(s * 0.5),
                     round(s * 0.68))
    painter.setBrush(QColor(theme.tokens().fg_dim))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QRectF(s * 0.46, s * 0.3, s * 0.08, s * 0.08))
    painter.end()
    return QIcon(pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation))


def badge_icon(size: int = 48) -> QPixmap:
    """认证徽章插图(校园登录页用): 圆环 + 对勾."""
    tk = theme.tokens()
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size * 2.0
    painter.setPen(_pen(tk.accent_hover, max(2.0, size * 0.08)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(s * 0.1, s * 0.1, s * 0.8, s * 0.8))
    painter.setPen(_pen(tk.accent, max(2.4, size * 0.1)))
    painter.drawLine(round(s * 0.32), round(s * 0.52),
                     round(s * 0.46), round(s * 0.66))
    painter.drawLine(round(s * 0.46), round(s * 0.66),
                     round(s * 0.7), round(s * 0.36))
    painter.end()
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
