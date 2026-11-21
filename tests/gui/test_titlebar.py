"""titlebar.py 单元测试: 无边框标题栏结构."""

from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton

from shuimu.gui import theme
from shuimu.gui.titlebar import TitleBar


def test_titlebar_has_three_buttons(qtbot):
    """标题栏有最小化/最大化/关闭按钮且高度固定(图标化)."""
    win = QMainWindow()
    qtbot.addWidget(win)
    title_bar = TitleBar(win, "测试标题")
    assert title_bar.height() == TitleBar.HEIGHT
    for btn in (title_bar.btn_min, title_bar.btn_max, title_bar.btn_close):
        assert not btn.icon().isNull()
        assert btn.text() == ""
    assert title_bar.btn_close.objectName() == "close"


def test_title_in_label(qtbot):
    """标题文本显示在标签里."""
    win = QMainWindow()
    qtbot.addWidget(win)
    title_bar = TitleBar(win, "水木工作台")
    labels = title_bar.findChildren(QLabel)
    assert any(lbl.text() == "水木工作台" for lbl in labels)


def test_log_button_wired_to_callback(qtbot):
    """传入 on_log 时生成日志按钮, 点击触发回调; 不传则无该按钮."""
    win = QMainWindow()
    qtbot.addWidget(win)
    called = []
    title_bar = TitleBar(win, "标题", on_log=lambda: called.append(True))
    log_btn = title_bar.findChild(QPushButton, "log")
    assert log_btn is not None and not log_btn.icon().isNull()
    log_btn.click()
    assert called == [True]
    plain_bar = TitleBar(win, "标题")
    assert plain_bar.findChild(QPushButton, "log") is None


def test_titlebar_settings_callback(qtbot):
    """设置按钮存在且携带按钮自身传给主题菜单回调."""
    win = QMainWindow()
    qtbot.addWidget(win)
    seen = []
    title_bar = TitleBar(win, "标题", on_settings=seen.append)
    btn = title_bar.findChild(QPushButton, "settings")
    assert btn is not None and not btn.icon().isNull()
    btn.click()
    assert seen == [btn]


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
