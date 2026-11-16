"""左栏课程树文件操作测试: 右键菜单语义 + 两栏剪贴板互通 + 粘贴自动登记."""

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QMessageBox

from shuimu.core.config import DEFAULTS
from shuimu.core.storage import Storage
from shuimu.gui.main_window import MainWindow


class _FakeInput:
    """替代 QInputDialog 的假件(返回固定 名称, ok).

    方法名须与 Qt 接口一致以便 monkeypatch 后被原样调用.
    """

    # pylint: disable=invalid-name
    def __init__(self, name):
        """记录希望 getText 返回的名称."""
        self._name = name

    def getText(self, *_args, **_kwargs):
        """返回 (构造时指定的名称, True)."""
        return self._name, True


class _FakeBox:
    """替代 QMessageBox 的假件(question 返回构造时指定按钮)."""

    StandardButton = QMessageBox.StandardButton

    def __init__(self, answer):
        """记录希望 question 返回的按钮."""
        self._answer = answer

    def question(self, *_args, **_kwargs):
        """返回构造时指定的按钮, 忽略全部参数."""
        return self._answer


@pytest.fixture()
def env(tmp_path, monkeypatch, qtbot):
    """主窗口 + 隔离回收站目录(默认课程"未分组"可用)."""
    cfg = deepcopy(DEFAULTS)
    cfg["fileops"]["trash_dir"] = str(tmp_path / "trash")
    monkeypatch.setattr("shuimu.core.controller.load_config",
                        lambda: deepcopy(cfg))
    storage = Storage(str(tmp_path / "ops.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "l.yaml"))
    qtbot.addWidget(window)
    yield window, storage, tmp_path
    storage.close()


def _setup_course(window, storage, tmp_path):
    """两文件同目录(虚拟节点 ws)+ 一链接; 返回 (课程id, 虚拟ws节点)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("a")
    (ws / "b.txt").write_text("b")
    cid = storage.list_courses()[0]["id"]
    window.ctl.add_resource(cid, str(ws / "a.txt"))
    window.ctl.add_resource(cid, str(ws / "b.txt"))
    window.ctl.add_resource(cid, "https://example.com/")
    window.reload_courses()
    course = window.course_tree.topLevelItem(0)
    return cid, course.child(0)      # child(0) = 虚拟 ws, child(1) = 链接


def _texts(menu):
    """菜单动作文字列表."""
    return [act.text() for act in menu.actions()]


class TestCourseMenu:
    """菜单形态随节点类型变化."""

    def test_menu_on_file_node(self, env):
        """文件节点: 打开/复制/剪切/重命名/删除, 无粘贴与新建."""
        window, _storage, tmp_path = env
        _cid, ws_node = _setup_course(window, _storage, tmp_path)
        menu = window._build_course_menu(ws_node.child(0))
        assert "打开" in _texts(menu)
        assert "复制" in _texts(menu) and "删除" in _texts(menu)
        assert "粘贴" not in _texts(menu)

    def test_menu_on_dir_node(self, env):
        """虚拟目录节点: 定位/粘贴/新建文件夹; 非登记无"移除资源"."""
        window, _storage, tmp_path = env
        _cid, ws_node = _setup_course(window, _storage, tmp_path)
        texts = _texts(window._build_course_menu(ws_node))
        assert "粘贴" in texts and "新建文件夹" in texts
        assert "在中栏定位" in texts and "移除资源" not in texts

    def test_menu_on_link_node(self, env):
        """链接节点只开放与移除, 无文件操作."""
        window, _storage, tmp_path = env
        _cid, ws_node = _setup_course(window, _storage, tmp_path)
        link_menu = _texts(window._build_course_menu(ws_node.parent().child(1)))
        assert "打开" in link_menu and "移除资源" in link_menu
        assert "复制" not in link_menu

    def test_menu_on_course_and_blank(self, env):
        """课程节点与空白处只保留撤销."""
        window, _storage, tmp_path = env
        _setup_course(window, _storage, tmp_path)
        course = window.course_tree.topLevelItem(0)
        assert _texts(window._build_course_menu(course)) == ["撤销"]
        assert _texts(window._build_course_menu(None)) == ["撤销"]

    def test_shortcuts_registered(self, env):
        """六个快捷键挂在课程树上, 与文件页同键位."""
        window, _storage, _tmp = env
        acts = {act.shortcut().toString()
                for act in window.course_tree.actions()
                if not act.shortcut().isEmpty()}
        assert acts == {"Ctrl+C", "Ctrl+X", "Ctrl+V", "Del", "F2", "Ctrl+Z"}


class TestClipboardShared:
    """两栏剪贴板互通."""

    def test_file_page_shares_app_clipboard(self, env):
        """文件页持引用即主窗口的应用剪贴板."""
        window, _storage, _tmp = env
        assert window.file_page._clipboard is window._app_clipboard

    def test_tree_copy_visible_to_file_page(self, env):
        """左栏复制后文件页立即可见(同一对象)."""
        window, storage, tmp_path = env
        _cid, ws_node = _setup_course(window, storage, tmp_path)
        window.course_tree.setCurrentItem(ws_node.child(0))
        window._course_copy()
        assert window.file_page._clipboard["mode"] == "copy"
        assert window.file_page._clipboard["paths"] \
            == [str(tmp_path / "ws" / "a.txt")]

    def test_copy_ignores_links_with_hint(self, env):
        """只选链接时提示无可操作项."""
        window, storage, tmp_path = env
        _cid, ws_node = _setup_course(window, storage, tmp_path)
        link = ws_node.parent().child(1)
        window.course_tree.setCurrentItem(link)
        window._course_copy()
        assert window.statusBar().currentMessage() == "请先选中文件或目录"


class TestPasteAndOps:
    """粘贴自动登记与各操作槽."""

    def test_paste_auto_registers(self, env):
        """贴入目录节点后新文件自动登记进该课程."""
        window, storage, tmp_path = env
        cid, ws_node = _setup_course(window, storage, tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        (other / "x.txt").write_text("new")
        window._app_clipboard.update(mode="copy", paths=[str(other / "x.txt")])
        window.course_tree.setCurrentItem(ws_node)
        window._course_paste()
        dst = tmp_path / "ws" / "x.txt"
        assert dst.exists()
        assert storage.find_resource(cid, str(dst)) is not None
        assert "已登记 1 项" in window.statusBar().currentMessage()

    def test_paste_conflict_replace(self, env, monkeypatch):
        """同名冲突选覆盖: 旧文件入回收站, 新文件登记."""
        window, storage, tmp_path = env
        cid, ws_node = _setup_course(window, storage, tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        (other / "a.txt").write_text("new")
        monkeypatch.setattr("shuimu.gui.dialogs.ask_conflict",
                            lambda _n, _r, _parent: ("replace", False))
        window._app_clipboard.update(mode="copy", paths=[str(other / "a.txt")])
        window.course_tree.setCurrentItem(ws_node)
        window._course_paste()
        dst = tmp_path / "ws" / "a.txt"
        assert dst.read_text() == "new"
        assert storage.find_resource(cid, str(dst)) is not None

    def test_paste_without_dir_hint(self, env):
        """选中文件节点粘贴时提示需要目录节点."""
        window, _storage, tmp_path = env
        _cid, ws_node = _setup_course(window, _storage, tmp_path)
        window._app_clipboard.update(mode="copy", paths=["/tmp/whatever"])
        window.course_tree.setCurrentItem(ws_node.child(0))
        window._course_paste()
        assert "目录节点" in window.statusBar().currentMessage()

    def test_delete_then_undo_restores_index(self, env, monkeypatch):
        """删除走回收站并移除索引行, 撤销两者俱恢复."""
        window, storage, tmp_path = env
        cid, ws_node = _setup_course(window, storage, tmp_path)
        target = tmp_path / "ws" / "a.txt"
        window.course_tree.setCurrentItem(ws_node.child(0))
        monkeypatch.setattr("shuimu.gui.main_window.QMessageBox",
                            _FakeBox(QMessageBox.StandardButton.Yes))
        window._course_delete()
        assert not target.exists()
        assert storage.find_resource(cid, str(target)) is None
        window._course_undo()
        assert target.exists()
        assert storage.find_resource(cid, str(target)) is not None

    def test_rename_via_slot_syncs_index(self, env, monkeypatch):
        """左栏重命名后索引路径同步."""
        window, storage, tmp_path = env
        cid, ws_node = _setup_course(window, storage, tmp_path)
        old = tmp_path / "ws" / "a.txt"
        window.course_tree.setCurrentItem(ws_node.child(0))
        monkeypatch.setattr("shuimu.gui.main_window.QInputDialog",
                            _FakeInput("c.txt"))
        window._course_rename()
        new = tmp_path / "ws" / "c.txt"
        assert new.exists() and not old.exists()
        assert storage.find_resource(cid, str(new)) is not None

    def test_mkdir_registers_into_course(self, env, monkeypatch):
        """目录节点下新建文件夹自动登记."""
        window, storage, tmp_path = env
        cid, ws_node = _setup_course(window, storage, tmp_path)
        window.course_tree.setCurrentItem(ws_node)
        monkeypatch.setattr("shuimu.gui.main_window.QInputDialog",
                            _FakeInput("新目录"))
        window._course_mkdir()
        created = tmp_path / "ws" / "新目录"
        assert created.is_dir()
        assert storage.find_resource(cid, str(created)) is not None
