"""左栏插件面板与页签显隐测试(offscreen, 真 MainWindow + 隔离插件状态)."""

from shuimu.core.storage import Storage
from shuimu.gui.main_window import MainWindow


def make_window(qtbot, tmp_path):
    """构造主窗口(插件状态经 tests/gui/conftest autouse 隔离)."""
    storage = Storage(str(tmp_path / "panel.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "links.yaml"))
    qtbot.addWidget(window)
    return window, storage


def panel_buttons(window):
    """插件面板 {插件id: 当前按钮文本}; id 取自 tooltip 首行."""
    result = {}
    for i in range(window.plugin_tree.topLevelItemCount()):
        item = window.plugin_tree.topLevelItem(i)
        plugin_id = item.toolTip(0).split()[0]
        button = window.plugin_tree.itemWidget(item, 1)
        result[plugin_id] = item.text(1) or button.text()
    return result


class TestPluginPanel:
    """面板列出全部插件与状态."""

    def test_all_builtin_listed(self, qtbot, tmp_path):
        """内置四插件全部在列; 未启用项灰显且按钮为\"启用\"."""
        window, storage = make_window(qtbot, tmp_path)
        buttons = panel_buttons(window)
        assert set(buttons) == {"hwcheck", "campus", "learn", "cloud",
                               "ai"}
        assert buttons["hwcheck"] == "禁用"      # read 插件默认启用
        assert buttons["campus"] == "启用"
        assert buttons["learn"] == "启用"
        assert buttons["cloud"] == "启用"
        storage.close()


class TestToggleAndTabs:
    """启用/禁用驱动页签显隐."""

    def test_enable_learn_shows_tab(self, qtbot, tmp_path, monkeypatch):
        """启用 learn -> 学堂页签可见; 禁用 -> 隐藏."""
        monkeypatch.setattr("shuimu.gui.main_window.confirm_network_enable",
                            lambda *_a, **_k: True)
        window, storage = make_window(qtbot, tmp_path)
        window._toggle_plugin("learn", True)    # pylint: disable=protected-access
        assert window.center_tabs.isTabVisible(2)
        assert panel_buttons(window)["learn"] == "禁用"
        window._toggle_plugin("learn", False)   # pylint: disable=protected-access
        assert not window.center_tabs.isTabVisible(2)
        assert panel_buttons(window)["learn"] == "启用"
        storage.close()

    def test_enable_campus_shows_right_tab(self, qtbot, tmp_path,
                                           monkeypatch):
        """启用 campus -> 右栏校园页签可见."""
        monkeypatch.setattr("shuimu.gui.main_window.confirm_network_enable",
                            lambda *_a, **_k: True)
        window, storage = make_window(qtbot, tmp_path)
        window._toggle_plugin("campus", True)   # pylint: disable=protected-access
        assert window.tabs.isTabVisible(2)
        storage.close()

    def test_enable_cancelled_keeps_tab_hidden(self, qtbot, tmp_path,
                                               monkeypatch):
        """网络确认框拒绝 -> 插件仍禁用, 页签保持隐藏."""
        monkeypatch.setattr("shuimu.gui.main_window.confirm_network_enable",
                            lambda *_a, **_k: False)
        window, storage = make_window(qtbot, tmp_path)
        window._toggle_plugin("cloud", True)    # pylint: disable=protected-access
        assert not window.center_tabs.isTabVisible(3)
        assert panel_buttons(window)["cloud"] == "启用"
        storage.close()

    def test_hide_active_tab_switches_to_first(self, qtbot, tmp_path,
                                               monkeypatch):
        """正在浏览的页签被禁用时切回首页签再隐藏."""
        monkeypatch.setattr("shuimu.gui.main_window.confirm_network_enable",
                            lambda *_a, **_k: True)
        window, storage = make_window(qtbot, tmp_path)
        window._toggle_plugin("cloud", True)    # pylint: disable=protected-access
        window.center_tabs.setCurrentWidget(window.cloud_page)
        window._toggle_plugin("cloud", False)   # pylint: disable=protected-access
        assert not window.center_tabs.isTabVisible(3)
        assert window.center_tabs.currentWidget() is window.file_page
        storage.close()
