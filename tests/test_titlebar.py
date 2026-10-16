"""titlebar.py 单元测试: 无边框标题栏结构."""

from PySide6.QtWidgets import QLabel, QMainWindow

from shuimu.titlebar import TitleBar


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
