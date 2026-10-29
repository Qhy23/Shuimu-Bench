"""titlebar.py 单元测试: 无边框标题栏结构."""

from PySide6.QtWidgets import QLabel, QMainWindow

from shuimu.gui import theme
from shuimu.gui.titlebar import TitleBar


def test_titlebar_has_three_buttons(qtbot):
    """标题栏有最小化/最大化/关闭按钮且高度固定."""
    win = QMainWindow()
    qtbot.addWidget(win)
    title_bar = TitleBar(win, "测试标题")
    assert title_bar.height() == TitleBar.HEIGHT
    assert title_bar.btn_min.text() == "—"
    assert title_bar.btn_max.text() == "▢"
    assert title_bar.btn_close.text() == "✕"
    assert title_bar.btn_close.objectName() == "close"


def test_title_in_label(qtbot):
    """标题文本显示在标签里."""
    win = QMainWindow()
    qtbot.addWidget(win)
    title_bar = TitleBar(win, "水木工作台")
    labels = title_bar.findChildren(QLabel)
    assert any(lbl.text() == "水木工作台" for lbl in labels)


def test_titlebar_logo_scales_with_font(qtbot):
    """logo 尺寸随全局字号更新."""
    win = QMainWindow()
    qtbot.addWidget(win)
    theme.apply_theme(win, 13)
    title_bar = TitleBar(win, "标题")
    small = title_bar.logo.pixmap().width()
    theme.apply_theme(win, 20)  # 真实链路: 先全局后联动
    title_bar.set_font_px(20)
    big = title_bar.logo.pixmap().width()
    assert 16 <= small < big
    theme.apply_theme(win, 13)
