"""界面主题: 设计 token 与全局 QSS 生成.

字号以 QSS 为单一来源(build_qss(font_px)); QSS 的字号不回写
QWidget.font(), 自绘部件须显式 setPixelSize 并经 set_font_px 通道联动.
缩放度量分两类: 文字邻近的随 k=font_px/DEFAULT_FONT_PX 缩放; 结构性
chrome(圆角/滚动条宽/边框)保持固定, 放大后显笨重.
"""

import hashlib

from ..core.config import DEFAULT_FONT_PX, clamp_font_px

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
MONO_FAMILY = '"JetBrainsMono NFM", "DejaVu Sans Mono", monospace'
ACCENT = "#3fc1a9"        # 手动日程强调色(青绿)

# 渐变 token(QSS qlineargradient 串): 半透明为 alpha 叠色模拟, Qt Widgets
# 无背景模糊(backdrop blur)
GRADIENT_PRIMARY = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                    f" stop:0 {PRIMARY}, stop:1 {PRIMARY_LIGHT})")
GRADIENT_PRIMARY_HOVER = ("qlineargradient(x1:0, y1:0, x2:1, y2:1,"
                          f" stop:0 {PRIMARY_LIGHT}, stop:1 #a44db5)")
GRADIENT_TITLEBAR = ("qlineargradient(x1:0, y1:0, x2:1, y2:0,"
                     f" stop:0 {PRIMARY_DARK}, stop:1 {PRIMARY})")
GRADIENT_CARD = ("qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                 f" stop:0 {BG_PANEL}, stop:1 #2b2b3e)")
PANEL_ALPHA = "rgba(38, 38, 55, 205)"

FONT_SIZE = f"{DEFAULT_FONT_PX}px"  # 默认字号快照, 兼容旧引用

_state = {"font_px": DEFAULT_FONT_PX}  # 当前字号, apply_theme 单点写入


# 课程稳定配色(借鉴 thu-info-app: md5(课程名)前 6 位 % 11 色板,
# 同名课全学期同色)
COURSE_PALETTE = ["#00a6f2", "#3cb3c8", "#fc6b50", "#f067bb", "#ef5b75",
                  "#7d7aea", "#bf73e5", "#ff9900", "#eebe00", "#5b8eff",
                  "#139161"]


def course_color(title: str) -> str:
    """课程色: md5(标题)前 6 位十六进制对色板长度取模."""
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()
    return COURSE_PALETTE[int(digest[:6], 16) % len(COURSE_PALETTE)]


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

    # 文字三级(对齐 thu-info-app 的 title/body/caption 阶梯)
    title_px = round(font_px * 1.35)
    caption_px = max(9, round(font_px * 0.85))

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
QLineEdit:focus, QTreeWidget:focus, QTreeView:focus, QTableWidget:focus,
QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus {{
    border: 1px solid {PRIMARY_LIGHT};
}}
QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: {pad(3)} {pad(6)};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {PRIMARY};
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
    font-size: {caption_px}px;
}}
QPushButton {{
    background-color: transparent;
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: {pad(5)} {pad(14)};
}}
QPushButton:hover {{
    border-color: {PRIMARY_LIGHT};
    color: {FG_TEXT};
}}
QPushButton:pressed {{
    background-color: {BG_HOVER};
}}
QPushButton[primary="true"] {{
    background-color: {GRADIENT_PRIMARY};
    border: none;
    color: {FG_TEXT};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {GRADIENT_PRIMARY_HOVER};
}}
QPushButton[primary="true"]:pressed {{
    background-color: {PRIMARY_DARK};
}}
QTabWidget::pane {{
    background-color: {PANEL_ALPHA};
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
    font-size: {caption_px}px;
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
QFrame#slotcard, QFrame#slotcard-manual {{
    background-color: {GRADIENT_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
}}
QFrame#cardbar {{
    border: none;
    border-radius: 1px;
}}
QPushButton#weektitle {{
    background: transparent;
    border: none;
    color: {FG_TEXT};
    font-weight: 600;
    font-size: {title_px}px;
    padding: {pad(2)} {pad(4)};
}}
QPushButton#weektitle:hover {{
    color: {PRIMARY_LIGHT};
}}
QWidget#weekpicker {{
    background-color: {PANEL_ALPHA};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
}}
QWidget#weekpicker QPushButton {{
    padding: {pad(2)} {pad(6)};
}}
QPushButton[today="true"] {{
    color: {PRIMARY_LIGHT};
    font-weight: 600;
    border-color: {PRIMARY_DARK};
}}
QLabel#apptitle {{
    color: {FG_TEXT};
    background: transparent;
    font-weight: 600;
    font-size: {title_px}px;
}}
QLabel#dayhead {{
    color: {FG_DIM};
    background: transparent;
    font-weight: 600;
    font-size: {title_px}px;
}}
QLabel#cardtitle {{
    color: {FG_TEXT};
    background: transparent;
}}
QLabel#cardtime {{
    color: {FG_DIM};
    background: transparent;
    font-family: {MONO_FAMILY};
    font-size: {caption_px}px;
}}
QLabel#cardloc {{
    color: {FG_DIM};
    background: transparent;
    font-size: {caption_px}px;
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
