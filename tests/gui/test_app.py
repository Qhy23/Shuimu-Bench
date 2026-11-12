"""GUI 冒烟测试: 主窗口三栏结构. 详细交互由人工演示核对."""

import os
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from shuimu.core import url_open
from shuimu.gui import main_window as shuimu_app
from shuimu.gui import theme
from shuimu.gui.main_window import MainWindow, tree_icon_size
from shuimu.core.storage import Storage


def test_main_window_structure(qtbot, tmp_path):
    """窗口有三栏, 默认课程已建立, 中栏文件/链接页签, 右栏整理/日程页签."""
    storage = Storage(str(tmp_path / "ui.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    assert window.splitter.count() == 3
    assert window.course_tree.topLevelItemCount() == 1  # 默认课程"未分组"
    assert window.center_tabs.count() == 2
    assert window.center_tabs.tabText(0) == "文件"
    assert window.center_tabs.tabText(1) == "链接"
    assert window.tabs.count() == 3
    assert [window.tabs.tabText(i) for i in range(3)] == ["整理", "日程",
                                                          "校园"]
    storage.close()


def test_main_window_frameless(qtbot, tmp_path):
    """主窗口无边框且带自绘标题栏."""
    storage = Storage(str(tmp_path / "ui_frameless.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.title_bar is not None
    storage.close()


def test_add_link_via_controller_shows_in_tree(qtbot, tmp_path):
    """经 controller 登记链接后, 课程树出现对应节点."""
    storage = Storage(str(tmp_path / "ui2.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://learn.tsinghua.edu.cn")
    window.reload_courses()
    course_node = window.course_tree.topLevelItem(0)
    assert course_node.childCount() == 1
    leaf = course_node.child(0)
    assert "learn.tsinghua.edu.cn" in leaf.text(0)
    assert leaf.toolTip(0) == "https://learn.tsinghua.edu.cn"
    storage.close()


def test_workspace_root_switch(qtbot, tmp_path, monkeypatch):
    """中栏工作目录可切换, 非法目录弹警告框且保持当前状态."""
    warnings = []
    monkeypatch.setattr("shuimu.gui.file_page.warn",
                        lambda _p, title, text: warnings.append((title, text)))
    storage = Storage(str(tmp_path / "ui3.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    sub = tmp_path / "sub"
    sub.mkdir()
    original = window.workspace_root
    assert window.set_workspace_root(str(sub))
    assert window.workspace_root == str(sub)
    assert window.file_page.root_edit.text() == str(sub)
    # 非法目录: 弹警告框, 拒绝并回退路径栏
    assert not window.set_workspace_root(str(tmp_path / "nonexistent"))
    assert len(warnings) == 1 and "无效的工作目录" in warnings[0][0]
    assert window.workspace_root == str(sub)
    assert window.file_page.root_edit.text() == str(sub)
    assert original != str(sub)
    storage.close()


def test_preview_bad_dir_warns(qtbot, tmp_path, monkeypatch):
    """整理页输入非法目录时弹警告框."""
    warnings = []
    monkeypatch.setattr("shuimu.gui.main_window.warn",
                        lambda _p, title, text: warnings.append((title, text)))
    storage = Storage(str(tmp_path / "ui4.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    window.dir_edit.setText(str(tmp_path / "nonexistent"))
    window._preview_plan()
    assert len(warnings) == 1 and "无法生成预览" in warnings[0][0]
    storage.close()


def test_resource_activated_opens_file(qtbot, tmp_path, monkeypatch):
    """双击文件资源用系统默认程序打开, 不改变中栏工作目录."""
    opened = []
    monkeypatch.setattr("shuimu.gui.main_window._open_local",
                        lambda path: opened.append(path) or True)
    storage = Storage(str(tmp_path / "ui5.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    f = tmp_path / "讲义.txt"
    f.write_text("x")
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, str(f))
    window.reload_courses()
    node = window.course_tree.topLevelItem(0).child(0)
    original_root = window.workspace_root
    window._on_resource_activated(node, 0)
    assert len(opened) == 1
    assert os.path.realpath(opened[0]) == os.path.realpath(str(f))
    assert window.workspace_root == original_root
    storage.close()


def test_resource_activated_dir_locates_in_browser(qtbot, tmp_path, monkeypatch):
    """双击目录资源仍在中栏定位, 不调用系统程序打开."""
    opened = []
    monkeypatch.setattr("shuimu.gui.main_window._open_local",
                        lambda path: opened.append(path) or True)
    storage = Storage(str(tmp_path / "ui6.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    sub = tmp_path / "课件"
    sub.mkdir()
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, str(sub))
    window.reload_courses()
    node = window.course_tree.topLevelItem(0).child(0)
    window._on_resource_activated(node, 0)
    assert not opened
    assert os.path.realpath(window.workspace_root) == os.path.realpath(str(sub))
    storage.close()


def test_browser_double_click_opens_file(qtbot, tmp_path, monkeypatch):
    """中栏双击文件用系统默认程序打开, 双击目录进入该目录."""
    opened = []
    monkeypatch.setattr("shuimu.gui.main_window._open_local",
                        lambda path: opened.append(path) or True)
    storage = Storage(str(tmp_path / "ui7.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    assert window.set_workspace_root(str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("x")
    d = tmp_path / "d"
    d.mkdir()
    qtbot.waitUntil(
        lambda: window.file_page.file_model.index(str(f)).isValid())
    window.file_page._on_double_clicked(
        window.file_page.file_model.index(str(f)))
    assert len(opened) == 1
    assert os.path.realpath(opened[0]) == os.path.realpath(str(f))
    qtbot.waitUntil(
        lambda: window.file_page.file_model.index(str(d)).isValid())
    window.file_page._on_double_clicked(
        window.file_page.file_model.index(str(d)))
    assert len(opened) == 1  # 目录是进入, 不是打开
    assert os.path.realpath(window.workspace_root) == os.path.realpath(str(d))
    storage.close()


def test_resource_activated_link_uses_open_url(qtbot, tmp_path, monkeypatch):
    """双击链接资源经 URL 分发层默认处理器打开."""
    opened = []
    monkeypatch.setattr(
        "shuimu.core.url_open._state",
        {"default": lambda url: opened.append(url) or True, "registry": {}})
    storage = Storage(str(tmp_path / "ui8.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://example.com/")
    window.reload_courses()
    node = window.course_tree.topLevelItem(0).child(0)
    window._on_resource_activated(node, 0)
    assert opened == ["https://example.com/"]
    storage.close()


def test_url_open_routes_to_gui_opener(monkeypatch):
    """GUI 打开方式注入分发层后, open_url 经 _open_url 走 QDesktopServices.

    (单测进程内 CLI/GUI 入口都会注册默认处理器, 直接断言全局残留会依赖
    测试执行顺序; 故此处显式注入 GUI 版验证整条分发链路.)
    """
    monkeypatch.setattr(url_open, "_state", {"default": None, "registry": {}})
    monkeypatch.setattr(shuimu_app, "_wslview_open", lambda t: None)
    desktop, calls = _fake_desktop()
    monkeypatch.setattr(shuimu_app, "QDesktopServices", desktop)
    url_open.set_default(shuimu_app._open_url)
    assert url_open.open_url("https://example.com") is True
    assert len(calls) == 1


def test_titlebar_log_button_opens_preview(qtbot, tmp_path):
    """标题栏日志按钮打开非模态预览, 显示留痕, 关闭后清引用."""
    storage = Storage(str(tmp_path / "logbtn.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    log_btn = window.title_bar.findChild(QPushButton, "log")
    assert log_btn is not None
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://example.com/")
    log_btn.click()
    dialog = window._log_dialog
    assert dialog is not None and dialog.isVisible()
    assert not dialog.isModal()
    actions = [dialog.table.item(row, 1).text()
               for row in range(dialog.table.rowCount())]
    assert "add_resource" in actions
    dialog.close()
    assert window._log_dialog is None  # 关闭后清引用, 可重建
    storage.close()


def _fake_desktop():
    """QDesktopServices 替身: openUrl 调用记录到第二个返回值."""
    calls = []

    def open_url(url):
        calls.append(url)
        return True

    return SimpleNamespace(openUrl=open_url), calls


def test_open_local_prefers_wslview(monkeypatch, tmp_path):
    """wslview 可用返回 True 时优先转发, 不调用 Qt."""
    f = tmp_path / "a.pdf"
    f.write_text("x")
    desktop, calls = _fake_desktop()
    seen = []
    monkeypatch.setattr("shuimu.gui.main_window._wslview_open",
                        lambda t: seen.append(t) or True)
    monkeypatch.setattr(shuimu_app, "QDesktopServices", desktop)
    assert shuimu_app._open_local(str(f))
    assert seen == [str(f)]
    assert not calls


def test_open_local_falls_back_to_qt(monkeypatch, tmp_path):
    """非 WSL 或未装 wslview 时回退 QDesktopServices."""
    f = tmp_path / "a.pdf"
    f.write_text("x")
    desktop, calls = _fake_desktop()
    monkeypatch.setattr("shuimu.gui.main_window._wslview_open", lambda t: None)
    monkeypatch.setattr(shuimu_app, "QDesktopServices", desktop)
    assert shuimu_app._open_local(str(f))
    assert len(calls) == 1


class TestZoom:
    """界面缩放快捷键行为."""

    def test_zoom_in_and_clamp(self, qtbot, tmp_path, qapp):
        """放大刷新 QSS 与网格, 到 20px 后夹住."""
        storage = Storage(str(tmp_path / "zoom.db"))
        window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
        qtbot.addWidget(window)
        window._zoom_in()
        assert theme.current_font_px() == 14
        assert "font-size: 14px" in qapp.styleSheet()
        assert "字号 14px" in window.statusBar().currentMessage()
        for _ in range(20):
            window._zoom_in()
        assert theme.current_font_px() == 20  # 夹住
        assert "已达最大字号" in window.statusBar().currentMessage()
        storage.close()

    def test_zoom_reset(self, qtbot, tmp_path):
        """Ctrl+0 语义: 重置到 config 默认字号."""
        storage = Storage(str(tmp_path / "zoom2.db"))
        window = MainWindow(storage, schedule_path=str(tmp_path / "s2.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
        qtbot.addWidget(window)
        window._zoom_in()
        window._zoom_in()
        window._zoom_reset()
        assert theme.current_font_px() == 13
        storage.close()


def test_tree_icons_scale_with_font(qtbot, tmp_path):
    """课程树节点图标随全局字号放大."""
    default_size = tree_icon_size()
    assert default_size == 16  # 默认 13px 字号
    storage = Storage(str(tmp_path / "icons.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://example.com/")
    window.reload_courses()
    node = window.course_tree.topLevelItem(0)
    small = max(s.width() for s in node.child(0).icon(0).availableSizes())
    assert small == default_size
    window.set_app_font(20)
    big = max(s.width() for s in
              window.course_tree.topLevelItem(0).child(0).icon(0).availableSizes())
    assert big == tree_icon_size() and big > small
    storage.close()
