"""GUI 冒烟测试: 主窗口三栏结构. 详细交互由人工演示核对."""

import os
from types import SimpleNamespace

from PySide6.QtCore import Qt

from shuimu import app as shuimu_app
from shuimu.app import MainWindow
from shuimu.storage import Storage


def test_main_window_structure(qtbot, tmp_path):
    """窗口有三栏, 默认课程已建立, 日志页可读."""
    storage = Storage(str(tmp_path / "ui.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    assert window.splitter.count() == 3
    assert window.course_tree.topLevelItemCount() == 1  # 默认课程"未分组"
    assert window.tabs.count() == 2
    assert "暂无操作记录" in window.log_view.toPlainText()
    storage.close()


def test_main_window_frameless(qtbot, tmp_path):
    """主窗口无边框且带自绘标题栏."""
    storage = Storage(str(tmp_path / "ui_frameless.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert window.title_bar is not None
    storage.close()


def test_add_link_via_controller_shows_in_tree(qtbot, tmp_path):
    """经 controller 登记链接后, 课程树出现对应节点."""
    storage = Storage(str(tmp_path / "ui2.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://learn.tsinghua.edu.cn")
    window.reload_courses()
    course_node = window.course_tree.topLevelItem(0)
    assert course_node.childCount() == 1
    assert "[链接]" in course_node.child(0).text(0)
    storage.close()


def test_workspace_root_switch(qtbot, tmp_path, monkeypatch):
    """中栏工作目录可切换, 非法目录弹警告框且保持当前状态."""
    warnings = []
    monkeypatch.setattr("shuimu.app._warn",
                        lambda _p, title, text: warnings.append((title, text)))
    storage = Storage(str(tmp_path / "ui3.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    sub = tmp_path / "sub"
    sub.mkdir()
    original = window.workspace_root
    assert window.set_workspace_root(str(sub))
    assert window.workspace_root == str(sub)
    assert window.root_edit.text() == str(sub)
    # 非法目录: 弹警告框, 拒绝并回退路径栏
    assert not window.set_workspace_root(str(tmp_path / "nonexistent"))
    assert len(warnings) == 1 and "无效的工作目录" in warnings[0][0]
    assert window.workspace_root == str(sub)
    assert window.root_edit.text() == str(sub)
    assert original != str(sub)
    storage.close()


def test_preview_bad_dir_warns(qtbot, tmp_path, monkeypatch):
    """整理页输入非法目录时弹警告框."""
    warnings = []
    monkeypatch.setattr("shuimu.app._warn",
                        lambda _p, title, text: warnings.append((title, text)))
    storage = Storage(str(tmp_path / "ui4.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    window.dir_edit.setText(str(tmp_path / "nonexistent"))
    window._preview_plan()
    assert len(warnings) == 1 and "无法生成预览" in warnings[0][0]
    storage.close()


def test_resource_activated_opens_file(qtbot, tmp_path, monkeypatch):
    """双击文件资源用系统默认程序打开, 不改变中栏工作目录."""
    opened = []
    monkeypatch.setattr("shuimu.app._open_local",
                        lambda path: opened.append(path) or True)
    storage = Storage(str(tmp_path / "ui5.db"))
    window = MainWindow(storage)
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
    monkeypatch.setattr("shuimu.app._open_local",
                        lambda path: opened.append(path) or True)
    storage = Storage(str(tmp_path / "ui6.db"))
    window = MainWindow(storage)
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
    monkeypatch.setattr("shuimu.app._open_local",
                        lambda path: opened.append(path) or True)
    storage = Storage(str(tmp_path / "ui7.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    assert window.set_workspace_root(str(tmp_path))
    f = tmp_path / "a.txt"
    f.write_text("x")
    d = tmp_path / "d"
    d.mkdir()
    qtbot.waitUntil(lambda: window.file_model.index(str(f)).isValid())
    window._on_browser_double_clicked(window.file_model.index(str(f)))
    assert len(opened) == 1
    assert os.path.realpath(opened[0]) == os.path.realpath(str(f))
    qtbot.waitUntil(lambda: window.file_model.index(str(d)).isValid())
    window._on_browser_double_clicked(window.file_model.index(str(d)))
    assert len(opened) == 1  # 目录是进入, 不是打开
    assert os.path.realpath(window.workspace_root) == os.path.realpath(str(d))
    storage.close()


def test_resource_activated_link_uses_open_url(qtbot, tmp_path, monkeypatch):
    """双击链接资源经 _open_url 打开."""
    opened = []
    monkeypatch.setattr("shuimu.app._open_url",
                        lambda url: opened.append(url) or True)
    storage = Storage(str(tmp_path / "ui8.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://example.com/")
    window.reload_courses()
    node = window.course_tree.topLevelItem(0).child(0)
    window._on_resource_activated(node, 0)
    assert opened == ["https://example.com/"]
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
    monkeypatch.setattr("shuimu.app._wslview_open",
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
    monkeypatch.setattr("shuimu.app._wslview_open", lambda t: None)
    monkeypatch.setattr(shuimu_app, "QDesktopServices", desktop)
    assert shuimu_app._open_local(str(f))
    assert len(calls) == 1


def test_wslview_open_dispatch(monkeypatch):
    """_wslview_open: 非 WSL 或无 wslview 返回 None, 否则调 wslview."""
    monkeypatch.setattr(shuimu_app, "_in_wsl", lambda: False)
    assert shuimu_app._wslview_open("/x/a.pdf") is None
    monkeypatch.setattr(shuimu_app, "_in_wsl", lambda: True)
    monkeypatch.setattr(shuimu_app.shutil, "which", lambda name: None)
    assert shuimu_app._wslview_open("/x/a.pdf") is None
    monkeypatch.setattr(shuimu_app.shutil, "which",
                        lambda name: "/usr/bin/wslview")

    class Result:
        """subprocess.run 结果替身."""

        returncode = 0

    ran = []
    monkeypatch.setattr(shuimu_app.subprocess, "run",
                        lambda cmd, check, timeout:
                        ran.append((cmd, check, timeout)) or Result())
    assert shuimu_app._wslview_open("/x/a.pdf") is True
    assert ran == [(["/usr/bin/wslview", "/x/a.pdf"], False, 15)]
