"""界面主题: 设计 token 与全局 QSS 生成.

字号以 QSS 为单一来源(build_qss(font_px)); QSS 的字号不回写
QWidget.font(), 自绘部件须显式 setPixelSize 并经 set_font_px 通道联动.
缩放度量分两类: 文字邻近的随 k=font_px/DEFAULT_FONT_PX 缩放; 结构性
chrome(圆角/滚动条宽/边框)保持固定, 放大后显笨重.
"""

# ---- 设计 token ----
PRIMARY = "#660874"        # 清华紫
PRIMARY_LIGHT = "#8c2f9e"  # 悬停态
PRIMARY_DARK = "#4c0556"   # 按下态
BG_BASE = "#1e1e2e"        # 窗口底色
BG_PANEL = "#262637"       # 面板/输入框底色
BG_HOVER = "#2f2f44"       # 条目悬停
FG_TEXT = "#e4e4ef"        # 主文字
FG_DIM = "#8a8a9e"         # 次要文字
BORDER = "#3a3a4e"         # 边框
DANGER = "#e5484d"         # 警示: 逾期/失败
RADIUS = "8px"
FONT_FAMILY = '"Noto Sans CJK SC", "Microsoft YaHei", sans-serif'

DEFAULT_FONT_PX = 13
MIN_FONT_PX = 9
MAX_FONT_PX = 20
FONT_SIZE = f"{DEFAULT_FONT_PX}px"  # 默认字号快照, 兼容旧引用

_state = {"font_px": DEFAULT_FONT_PX}  # 当前字号, apply_theme 单点写入


def clamp_font_px(value) -> int:
    """字号夹取到 [MIN_FONT_PX, MAX_FONT_PX]; 非法值回退默认."""
    try:
        px = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FONT_PX
    return max(MIN_FONT_PX, min(MAX_FONT_PX, px))


def current_font_px() -> int:
    """当前生效字号."""
    return _state["font_px"]


def build_qss(font_px: int = DEFAULT_FONT_PX) -> str:
    """按字号生成全局 QSS; 文字邻近 padding 随 k 缩放, chrome 固定."""
    font_px = clamp_font_px(font_px)
    k = font_px / DEFAULT_FONT_PX

    def pad(base: int) -> str:
        """文字邻近内边距按 k 缩放, 下限 2px."""
        return f"{max(2, round(base * k))}px"

    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: {font_px}px;
    color: {FG_TEXT};
}}
QMainWindow, QWidget {{
    background-color: {BG_BASE};
}}
QTreeWidget, QTreeView, QTableWidget, QPlainTextEdit, QLineEdit {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: {pad(4)};
    selection-background-color: {PRIMARY};
}}
QTreeWidget::item, QTreeView::item {{
    padding: {pad(4)} {pad(2)};
    border-radius: 4px;
}}
QTreeWidget::item:hover, QTreeView::item:hover {{
    background-color: {BG_HOVER};
}}
QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {PRIMARY};
}}
QHeaderView::section {{
    background-color: {BG_BASE};
    color: {FG_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: {pad(4)} {pad(6)};
}}
QPushButton {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: {pad(5)} {pad(14)};
}}
QPushButton:hover {{
    background-color: {PRIMARY_LIGHT};
    border-color: {PRIMARY_LIGHT};
}}
QPushButton:pressed {{
    background-color: {PRIMARY_DARK};
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {BG_BASE};
    color: {FG_DIM};
    padding: {pad(6)} {pad(16)};
    border-top-left-radius: {RADIUS};
    border-top-right-radius: {RADIUS};
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {BG_PANEL};
    color: {FG_TEXT};
    border-bottom: 2px solid {PRIMARY};
}}
QTabBar::tab:hover:!selected {{
    color: {FG_TEXT};
}}
QSplitter::handle {{
    background-color: {BG_BASE};
    width: 4px;
}}
QSplitter::handle:hover {{
    background-color: {PRIMARY};
}}
QLabel {{
    color: {FG_DIM};
    background: transparent;
    border: none;
}}
QStatusBar {{
    background-color: {BG_BASE};
    color: {FG_DIM};
    border-top: 1px solid {BORDER};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {PRIMARY_LIGHT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {PRIMARY_LIGHT};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QToolTip {{
    background-color: {BG_PANEL};
    color: {FG_TEXT};
    border: 1px solid {BORDER};
    padding: {pad(4)} {pad(8)};
}}
QFrame#daycol, QFrame#daycol-today {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QFrame#daycol-today {{
    background-color: {BG_HOVER};
    border: 1px solid {PRIMARY};
}}
QFrame#slotcard {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QLabel#dayhead {{
    color: {FG_DIM};
    background: transparent;
    font-weight: 600;
}}
QLabel#cardtitle {{
    color: {FG_TEXT};
    background: transparent;
}}
QLabel#warnbar {{
    color: {DANGER};
    background: transparent;
}}
"""


QSS = build_qss()


def apply_theme(app, font_px: int = DEFAULT_FONT_PX) -> None:
    """把主题(含字号)应用到 QApplication."""
    font_px = clamp_font_px(font_px)
    _state["font_px"] = font_px
    app.setStyleSheet(build_qss(font_px))
