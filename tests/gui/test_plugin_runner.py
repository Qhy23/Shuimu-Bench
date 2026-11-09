"""plugin_runner 与文件页插件菜单测试(offscreen, 不真实 exec 参数框)."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QSpinBox,
)

from shuimu.core.pluginapi import finding, make_report
from shuimu.core.pluginman import parse_manifest
from shuimu.core.storage import Storage
from shuimu.gui import theme
from shuimu.gui.file_page import FileBrowserPage
from shuimu.gui.main_window import MainWindow
from shuimu.gui.plugin_runner import (
    PluginResultDialog,
    PluginRunDialog,
    _severity_color,
)


def manifest_with(params, applies_to=("dir",)):
    """造一个带 params 声明的 manifest."""
    raw = {"id": "hwcheck", "name": "作业提交自查", "version": "1",
           "applies_to": list(applies_to), "params": params}
    return parse_manifest(raw)[0]


class TestRunDialog:
    """声明式参数表单."""

    def test_field_types_and_defaults(self, qtbot):
        """五种参数类型映射到对应控件并回读默认值."""
        manifest = manifest_with([
            {"name": "s", "type": "str", "label": "字符串", "default": "x"},
            {"name": "b", "type": "bool", "label": "布尔", "default": True},
            {"name": "i", "type": "int", "label": "整数", "default": 7},
            {"name": "c", "type": "choice", "label": "选择", "default": "b",
             "choices": [["a", "甲"], ["b", "乙"]]},
            {"name": "p", "type": "path", "label": "路径"},
        ])
        dialog = PluginRunDialog(manifest, ["/tmp/target"])
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "运行插件 - 作业提交自查"
        assert isinstance(dialog._fields["s"], QLineEdit)
        assert isinstance(dialog._fields["b"], QCheckBox)
        assert isinstance(dialog._fields["i"], QSpinBox)
        assert isinstance(dialog._fields["c"], QComboBox)
        assert isinstance(dialog._fields["p"], QLineEdit)
        values = dialog.values()
        assert values["s"] == "x"
        assert values["b"] is True
        assert values["i"] == 7
        assert values["c"] == "b"
        assert values["p"] == ""   # 空串由 validate 阶段视为未提供

    def test_accept_invalid_params_warns(self, qtbot, monkeypatch):
        """校验失败点 Ok 弹警告且不关闭对话框."""
        manifest = manifest_with(
            [{"name": "n", "type": "str", "label": "N", "required": True}])
        dialog = PluginRunDialog(manifest, ["/tmp/target"])
        qtbot.addWidget(dialog)
        warned = []
        monkeypatch.setattr("shuimu.gui.plugin_runner.warn",
                            lambda parent, title, text: warned.append(title))
        dialog._accept()
        assert warned == ["参数无效"]
        assert not dialog.result()   # 未 accept


class TestResultDialog:
    """结果对话框与严重度着色."""

    def test_rows_and_colors(self, qtbot):
        """findings 按原顺序入表, 严重度列着色."""
        report = make_report([
            finding("error", "secret_scan", "疑似密钥", "a.py:1"),
            finding("warn", "large_file", "大文件 x.bin"),
            finding("ok", "required_files", "已包含 报告 PDF", "report.pdf"),
        ])
        dialog = PluginResultDialog("作业提交自查", report)
        qtbot.addWidget(dialog)
        assert dialog.table.rowCount() == 3
        assert dialog.table.item(0, 0).text() == "错误"
        assert dialog.table.item(0, 0).foreground().color() == QColor(
            theme.DANGER)
        assert dialog.table.item(1, 0).foreground().color() == QColor(
            theme.WARN)
        assert dialog.table.item(2, 0).foreground().color() == QColor(
            theme.FG_DIM)
        assert dialog.table.item(0, 3).text() == "a.py:1"

    def test_severity_color_map(self):
        """三档严重度色映射."""
        assert _severity_color("error") == QColor(theme.DANGER)
        assert _severity_color("warn") == QColor(theme.WARN)
        assert _severity_color("ok") == QColor(theme.FG_DIM)


class TestFilePagePluginMenu:
    """文件页右键菜单的插件子菜单."""

    def _page(self, qtbot, tmp_path):
        page = FileBrowserPage(workspace_root=str(tmp_path))
        qtbot.addWidget(page)
        return page

    def _select(self, monkeypatch, page, path):
        monkeypatch.setattr(page, "selected_paths", lambda: [path])

    def test_submenu_appears_for_applicable(self, qtbot, tmp_path,
                                            monkeypatch):
        """目录选中且插件适用时出现插件子菜单."""
        page = self._page(qtbot, tmp_path)
        page.set_plugin_catalog([manifest_with([])])
        self._select(monkeypatch, page, str(tmp_path))
        menu = page._build_context_menu(True, True)
        sub = next(action.menu() for action in menu.actions()
                   if action.text() == "插件")
        assert [act.text() for act in sub.actions()] == ["作业提交自查"]

    def test_submenu_hidden_on_kind_mismatch(self, qtbot, tmp_path,
                                             monkeypatch):
        """插件仅适用文件时, 目录选中不出现子菜单."""
        page = self._page(qtbot, tmp_path)
        page.set_plugin_catalog(
            [manifest_with([], applies_to=("file",))])
        self._select(monkeypatch, page, str(tmp_path))
        menu = page._build_context_menu(True, True)
        assert "插件" not in [action.text() for action in menu.actions()]

    def test_submenu_hidden_without_catalog(self, qtbot, tmp_path):
        """无可用插件时不出现子菜单(与既有菜单断言兼容)."""
        page = self._page(qtbot, tmp_path)
        menu = page._build_context_menu(True, True)
        assert "插件" not in [action.text() for action in menu.actions()]

    def test_trigger_emits_signal(self, qtbot, tmp_path, monkeypatch):
        """点击插件菜单项发出 plugin_requested(插件 id, 选中路径)."""
        page = self._page(qtbot, tmp_path)
        page.set_plugin_catalog([manifest_with([])])
        self._select(monkeypatch, page, str(tmp_path))
        received = []
        page.plugin_requested.connect(
            lambda pid, paths: received.append((pid, paths)))
        menu = page._build_context_menu(True, True)
        sub = next(action.menu() for action in menu.actions()
                   if action.text() == "插件")
        sub.actions()[0].trigger()
        assert received == [("hwcheck", [str(tmp_path)])]


class TestMainWindowSmoke:
    """主窗口编排冒烟(对话框 exec 打桩)."""

    def test_plugin_request_cancelled_logs(self, qtbot, tmp_path, monkeypatch):
        """参数框取消 -> 留痕 cancelled, 状态栏提示已取消."""
        storage = Storage(str(tmp_path / "ui.db"))
        window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                            links_path=str(tmp_path / "links.yaml"))
        qtbot.addWidget(window)
        monkeypatch.setattr("shuimu.gui.plugin_runner.PluginRunDialog.exec",
                            lambda self: 0)   # 0 = Rejected
        window._on_plugin_requested("hwcheck", [str(tmp_path)])
        assert "已取消" in window.statusBar().currentMessage()
        op = storage.list_operations(1)[0]
        assert op["action"] == "plugin:hwcheck"
        assert op["status"] == "cancelled"
        storage.close()
