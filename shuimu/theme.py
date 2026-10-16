"""界面主题: 设计 token 与全局 QSS.

集中在这一处定义配色/圆角/间距, main.py 在 QApplication 上应用.
与未来 QML 迁移解耦: 换框架时只换应用方式, token 直接平移.
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
RADIUS = "8px"
FONT_SIZE = "13px"

QSS = f"""
* {{
    font-size: {FONT_SIZE};
    color: {FG_TEXT};
}}
QMainWindow, QWidget {{
    background-color: {BG_BASE};
}}
QTreeWidget, QTreeView, QTableWidget, QPlainTextEdit, QLineEdit {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: 4px;
    selection-background-color: {PRIMARY};
}}
QTreeWidget::item, QTreeView::item {{
    padding: 4px 2px;
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
    padding: 4px 6px;
}}
QPushButton {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: 5px 14px;
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
    padding: 6px 16px;
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
    padding: 4px 8px;
}}
"""


def apply_theme(app) -> None:
    """把主题应用到 QApplication."""
    app.setStyleSheet(QSS)
