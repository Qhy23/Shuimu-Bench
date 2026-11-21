"""GUI 冒烟测试: 主窗口三栏结构. 详细交互由人工演示核对."""

import os
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from shuimu.core import url_open
from shuimu.gui import artwork as shuimu_gui_artwork
from shuimu.gui import main_window as shuimu_app
from shuimu.gui import theme
from shuimu.gui.main_window import MainWindow, tree_icon_size
from shuimu.core.storage import Storage


def test_main_window_structure(qtbot, tmp_path):
    """窗口有三栏; 中栏四页签/右栏三页签, 服务型插件页签默认隐藏."""
    storage = Storage(str(tmp_path / "ui.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    assert window.splitter.count() == 3
    assert window.course_tree.topLevelItemCount() == 1  # 默认课程"未分组"
    assert window.center_tabs.count() == 4
    assert window.center_tabs.tabText(0) == "文件"
    assert window.center_tabs.tabText(1) == "链接"
    assert window.center_tabs.tabText(2) == "学堂"
    assert window.center_tabs.tabText(3) == "云盘"
    assert window.center_tabs.isTabVisible(0)
    assert not window.center_tabs.isTabVisible(2)   # 学堂: 未启用隐藏
    assert not window.center_tabs.isTabVisible(3)   # 云盘: 未启用隐藏
    assert window.tabs.count() == 4
    assert [window.tabs.tabText(i) for i in range(4)] == ["整理", "日程",
                                                          "校园", "AI"]
    assert not window.tabs.isTabVisible(2)          # 校园: 未启用隐藏
    assert not window.tabs.isTabVisible(3)          # AI: 未启用隐藏
    assert window.title_bar.findChild(QPushButton, "settings") is not None
    # 左栏插件面板: 内置插件全部列出(含未启用)
    assert window.plugin_tree.topLevelItemCount() == 5
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


def test_add_directory_imports_as_folders(qtbot, tmp_path):
    """中栏目录"加入课程"递归导入: 虚拟文件夹 + 文件索引, 不建目录索引."""
    storage = Storage(str(tmp_path / "ui6.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "sched.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    sub = tmp_path / "课件"
    (sub / "第1周").mkdir(parents=True)
    (sub / "README.md").write_text("x")
    (sub / "第1周" / "第7讲.pdf").write_text("y")
    course_id = storage.list_courses()[0]["id"]
    window.course_tree.setCurrentItem(window.course_tree.topLevelItem(0))
    window._on_add_requested(str(sub))
    folders = storage.list_folders(course_id)
    names = {f["name"] for f in folders}
    assert names == {"课件", "第1周"}          # 两级虚拟文件夹
    kinds = {r["kind"] for r in storage.list_resources(course_id)}
    assert kinds == {"file"}                   # 无目录索引行
    assert storage.find_resource(course_id, str(sub / "README.md")) is not None
    status = window.statusBar().currentMessage()
    assert "已导入目录 课件" in status and "2 个文件" in status
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


def test_theme_switch_refreshes_all_panels(qtbot, tmp_path, qapp,
                                           monkeypatch):
    """运行时切浅色后各服务页签与已打开留痕按新 token 更新."""
    monkeypatch.setattr("shuimu.gui.theme.user_themes_dir",
                        lambda: str(tmp_path / "themes"))
    storage = Storage(str(tmp_path / "theme.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    window._open_log_dialog()
    old_dialog = window._log_dialog
    assert theme.current_name() == "dark"
    window.set_app_theme("light")
    assert theme.current_name() == "light"
    assert theme.LIGHT_TOKENS.base in qapp.styleSheet()
    assert window.ai_tab.status_label.styleSheet() == \
        f"color: {theme.LIGHT_TOKENS.fg_dim};"
    assert window.learn_page.refreshed_label.styleSheet() == \
        f"color: {theme.LIGHT_TOKENS.fg_dim};"
    assert window.cloud_page.share_label.styleSheet() == \
        f"color: {theme.LIGHT_TOKENS.fg_dim};"
    assert window._log_dialog is None
    window._open_log_dialog()
    assert window._log_dialog is not old_dialog
    assert window._log_dialog.isVisible()
    window._log_dialog.close()
    window.set_app_theme("dark")
    assert theme.current_name() == "dark"
    storage.close()


def test_config_theme_applied_before_panels(qtbot, tmp_path, monkeypatch):
    """config app.theme 在主窗口构建前生效."""
    monkeypatch.setattr("shuimu.gui.main_window.theme_name_value",
                        lambda _cfg: "light")
    monkeypatch.setattr("shuimu.gui.theme.user_themes_dir",
                        lambda: str(tmp_path / "themes"))
    storage = Storage(str(tmp_path / "config-theme.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    assert theme.current_name() == "light"
    window.set_app_theme("dark")
    storage.close()


def test_tree_icons_scale_with_font(qtbot, tmp_path):
    """课程树节点图标随全局字号放大, 不重建节点."""
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
    leaf = node.child(0)
    small = max(s.width() for s in leaf.icon(0).availableSizes())
    assert small == default_size
    window.set_app_font(20)
    assert window.course_tree.topLevelItem(0) is node
    assert node.child(0) is leaf
    big = max(s.width() for s in leaf.icon(0).availableSizes())
    assert big == tree_icon_size() and big > small
    storage.close()


def test_zoom_updates_icons_without_reloading_or_disk_checks(
        qtbot, tmp_path, monkeypatch):
    """缩放不重建课程树, 不查询资源或检查磁盘, 保留选中和展开状态."""
    storage = Storage(str(tmp_path / "zoom-tree.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    cid = storage.ensure_default_course()
    fid = storage.add_folder(cid, None, "课件")
    missing = tmp_path / "missing.pdf"
    storage.add_resource(cid, "file", str(missing), folder_id=fid)
    window.reload_courses()
    course = window.course_tree.topLevelItem(0)
    folder = course.child(0)
    leaf = folder.child(0)
    course.setExpanded(True)
    folder.setExpanded(True)
    window.course_tree.setCurrentItem(leaf)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("字号缩放不应重载索引或检查磁盘")

    monkeypatch.setattr(window, "reload_courses", forbidden)
    monkeypatch.setattr(storage, "get_course", forbidden)
    monkeypatch.setattr(storage, "get_resource", forbidden)
    monkeypatch.setattr(Storage, "check_resource", staticmethod(forbidden))
    window.set_app_font(20)
    assert window.course_tree.currentItem() is leaf
    assert course.isExpanded() and folder.isExpanded()
    assert leaf.text(0).endswith("(失效)")
    assert max(s.width() for s in leaf.icon(0).availableSizes()) \
        == tree_icon_size()
    assert max(s.width() for s in folder.icon(0).availableSizes()) \
        == tree_icon_size()
    storage.close()


def test_course_link_uses_cached_favicon_and_survives_zoom(qtbot, tmp_path):
    """左栏链接用 favicon 缓存, 缩放时不会被默认 WEB 覆盖."""
    storage = Storage(str(tmp_path / "link-icon.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    icon_path = tmp_path / "site.png"
    shuimu_app.file_icons._draw_type_icon("OK", "#3fb950", 16).save(str(icon_path))
    window.favicon_service.store.cached_icon_path = lambda _url: str(icon_path)
    storage.add_resource(storage.ensure_default_course(), "link",
                         "https://example.com/")
    window.reload_courses()
    leaf = window.course_tree.topLevelItem(0).child(0)
    expected = leaf.icon(0).pixmap(16, 16).toImage()
    window.set_app_font(20)
    assert leaf.icon(0).pixmap(16, 16).toImage() == expected
    storage.close()


def test_course_link_fetches_and_updates_after_enable(qtbot, tmp_path):
    """开启联网后重新登记左栏链接, 后台结果到达即更新图标."""
    storage = Storage(str(tmp_path / "link-fetch.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    icon_path = tmp_path / "site.png"
    shuimu_app.file_icons._draw_type_icon("OK", "#3fb950", 16).save(str(icon_path))
    calls = []
    def fetch(url):
        calls.append(url)
        return str(icon_path)

    window.favicon_service.store.get = fetch
    storage.add_resource(storage.ensure_default_course(), "link",
                         "https://example.com/")
    window.reload_courses()
    leaf = window.course_tree.topLevelItem(0).child(0)
    expected = leaf.icon(0).pixmap(16, 16).toImage()
    window._toggle_favicons(True)
    qtbot.waitUntil(lambda: bool(calls), timeout=5000)
    qtbot.waitUntil(lambda: leaf.icon(0).pixmap(16, 16).toImage() != expected,
                    timeout=5000)
    assert "https://example.com/" in calls  # 左栏链接开启后确实进入后台获取
    window.close()
    storage.close()


def test_course_tree_uses_file_type_icons(qtbot, tmp_path, monkeypatch):
    """首次构建课程树按后缀取文件图标, 而不是通用文件图标."""
    storage = Storage(str(tmp_path / "file-icons.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    cid = storage.ensure_default_course()
    for name in ("讲义.pdf", "代码.py"):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        storage.add_resource(cid, "file", str(path))
    calls = []
    original = shuimu_app.file_icons.file_type_icon

    def record(path, **kwargs):
        calls.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(shuimu_app.file_icons, "file_type_icon", record)
    window.reload_courses()
    assert calls == [str(tmp_path / "讲义.pdf"), str(tmp_path / "代码.py")]
    storage.close()


def test_zoom_repeated_same_size_skips_icon_repaints(qtbot, tmp_path,
                                                     monkeypatch):
    """相同字号/主题重复刷新不重复生成树图标."""
    storage = Storage(str(tmp_path / "zoom-cache.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    storage.add_resource(storage.ensure_default_course(),
                         "link", "https://example.com/")
    window.reload_courses()
    calls = []
    original = shuimu_gui_artwork.kind_icon

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(shuimu_gui_artwork, "kind_icon", record)
    record.cache_clear = original.cache_clear  # 主题切换仍需能清缓存
    window.set_app_font(14)
    count = len(calls)
    assert count > 0
    window.set_app_font(14)
    assert len(calls) == count + 2  # 仅链接页两个图标, 课程树无重绘
    assert calls[count:] == [(("web",), {"size": 18}),
                             (("dir",), {"size": 18})]
    window.set_app_theme("light")
    assert len(calls) > count  # 主题代次变化必须刷新图标颜色
    window.set_app_theme("dark")
    storage.close()
