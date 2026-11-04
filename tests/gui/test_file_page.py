"""file_page.py 单元测试: 文件页资源管理器操作(复制/剪切/粘贴/删除/重命名/新建).

断言一律落在磁盘事实(文件存在性)而非视图行数: QFileSystemModel 的
文件系统监听是异步的. 对话框经 monkeypatch 替换模块内引用, 不弹窗.
"""

import json
from copy import deepcopy

import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QMessageBox

from shuimu.core.config import DEFAULTS
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage
from shuimu.gui.file_page import FileBrowserPage


@pytest.fixture()
def page(tmp_path, monkeypatch, qtbot):
    """带独立 controller 与回收站目录的文件页(workspace 指向临时目录)."""
    monkeypatch.setattr("shuimu.core.controller.template_schedule_path",
                        lambda: str(tmp_path / "no-such-template.yaml"))
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-such-links-template.yaml"))
    cfg = deepcopy(DEFAULTS)
    cfg["fileops"]["trash_dir"] = str(tmp_path / "trash")
    storage = Storage(str(tmp_path / "page.db"))
    ctl = Controller(storage, config=cfg,
                     schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(tmp_path / "links.yaml"))
    ws = tmp_path / "ws"
    ws.mkdir()
    page = FileBrowserPage(ctl, workspace_root=str(ws))
    qtbot.addWidget(page)
    yield page, ctl, ws
    storage.close()


class _FakeBox:
    """替代 QMessageBox 的假件(可配置 question 返回值)."""

    StandardButton = QMessageBox.StandardButton

    def __init__(self, answer):
        """记录希望 question 返回的按钮."""
        self._answer = answer

    def question(self, *_args, **_kwargs):
        """返回构造时指定的按钮, 忽略全部参数."""
        return self._answer


class TestSelection:
    """多选与选中读取."""

    def test_selected_paths_multi(self, page, qtbot):
        """多选时 selected_paths 返回全部, 无选中为空."""
        page, _ctl, ws = page
        for name in ("a.txt", "b.txt"):
            (ws / name).write_text(name)
        idx_a = page.file_model.index(str(ws / "a.txt"))
        idx_b = page.file_model.index(str(ws / "b.txt"))
        qtbot.waitUntil(lambda: idx_a.isValid() and idx_b.isValid())
        sel = page.file_view.selectionModel()
        for idx in (idx_a, idx_b):
            sel.select(idx, QItemSelectionModel.SelectionFlag.Select
                       | QItemSelectionModel.SelectionFlag.Rows)
        assert page.selected_paths() == [str(ws / "a.txt"), str(ws / "b.txt")]
        sel.clear()
        assert page.selected_paths() == []

    def test_without_controller(self, tmp_path, qtbot):
        """未注入 controller 也可构造, 浏览 API 不受影响."""
        bare = FileBrowserPage(workspace_root=str(tmp_path))
        qtbot.addWidget(bare)
        assert bare.workspace_root == str(tmp_path)
        assert bare.selected_paths() == []


class TestClipboardOps:
    """复制/剪切/粘贴."""

    def test_copy_then_paste_twice(self, page, qtbot):
        """复制可多次粘贴, 重名自动 _1; 剪贴板保留."""
        page, _ctl, ws = page
        other = ws.parent / "other"
        other.mkdir()
        (other / "a.txt").write_text("x")
        page._clipboard = {"mode": "copy", "paths": [str(other / "a.txt")]}
        page._paste()
        qtbot.waitUntil((ws / "a.txt").exists)
        page._paste()
        qtbot.waitUntil((ws / "a_1.txt").exists)
        assert page._clipboard["paths"] == [str(other / "a.txt")]  # 保留可再贴

    def test_cut_then_paste_clears_clipboard(self, page, qtbot):
        """剪切粘贴后源消失, 剪贴板清空, 再粘贴提示为空."""
        page, _ctl, ws = page
        received = []
        page.status_message.connect(received.append)
        other = ws.parent / "other"
        other.mkdir()
        (other / "b.txt").write_text("x")
        page._clipboard = {"mode": "cut", "paths": [str(other / "b.txt")]}
        page._paste()
        qtbot.waitUntil((ws / "b.txt").exists)
        assert not (other / "b.txt").exists()
        assert page._clipboard["paths"] == []
        page._paste()
        assert "剪贴板为空" in received

    def test_paste_without_ctl(self, tmp_path, qtbot):
        """未注入 controller 时粘贴给出不可用提示."""
        bare = FileBrowserPage(workspace_root=str(tmp_path))
        qtbot.addWidget(bare)
        received = []
        bare.status_message.connect(received.append)
        bare._clipboard = {"mode": "copy", "paths": [str(tmp_path / "x")]}
        bare._paste()
        assert any("控制器不可用" in msg for msg in received)

    def test_copy_selected_empty_hint(self, page):
        """空选中复制给提示."""
        page, _ctl, _ws = page
        received = []
        page.status_message.connect(received.append)
        page._copy_selected()
        assert any("请先选中" in msg for msg in received)

    def test_paste_into_selected_dir(self, page, qtbot):
        """恰好选中一个目录时粘贴进该目录(树内展开浏览场景)."""
        page, _ctl, ws = page
        sub = ws / "sub"
        sub.mkdir()
        (sub / "inner.txt").write_text("x")
        idx = page.file_model.index(str(sub))
        qtbot.waitUntil(idx.isValid)
        page.file_view.setCurrentIndex(idx)
        other = ws.parent / "other"
        other.mkdir()
        (other / "p.txt").write_text("p")
        page._clipboard = {"mode": "copy", "paths": [str(other / "p.txt")]}
        assert page._paste_target() == str(sub)
        page._paste()
        qtbot.waitUntil((sub / "p.txt").exists)

    def test_paste_target_rules(self, page, qtbot):
        """选中文件/多选/无选中时目标回退到当前工作目录."""
        page, _ctl, ws = page
        f = ws / "a.txt"
        f.write_text("x")
        sub = ws / "sub"
        sub.mkdir()
        idx_f = page.file_model.index(str(f))
        idx_s = page.file_model.index(str(sub))
        qtbot.waitUntil(lambda: idx_f.isValid() and idx_s.isValid())
        sel = page.file_view.selectionModel()
        # 无选中 -> 工作目录
        assert page._paste_target() == str(ws)
        # 选中单个文件 -> 工作目录(文件不是粘贴目标)
        sel.select(idx_f, QItemSelectionModel.SelectionFlag.ClearAndSelect
                   | QItemSelectionModel.SelectionFlag.Rows)
        assert page._paste_target() == str(ws)
        # 文件与目录多选 -> 工作目录
        sel.select(idx_s, QItemSelectionModel.SelectionFlag.Select
                   | QItemSelectionModel.SelectionFlag.Rows)
        assert page._paste_target() == str(ws)
        # 恰好一个目录 -> 该目录
        sel.select(idx_f, QItemSelectionModel.SelectionFlag.Deselect
                   | QItemSelectionModel.SelectionFlag.Rows)
        assert page._paste_target() == str(sub)

    def test_right_click_switches_selection(self, page, qtbot):
        """右键未选中的条目时, 选择切到该条目."""
        page, _ctl, ws = page
        for name in ("a.txt", "b.txt"):
            (ws / name).write_text(name)
        idx_a = page.file_model.index(str(ws / "a.txt"))
        idx_b = page.file_model.index(str(ws / "b.txt"))
        qtbot.waitUntil(lambda: idx_a.isValid() and idx_b.isValid())
        page.file_view.setCurrentIndex(idx_a)
        assert page.selected_paths() == [str(ws / "a.txt")]
        page._ensure_clicked_selected(idx_b)
        assert page.selected_paths() == [str(ws / "b.txt")]
        # 已在选中集内的条目不重置多选
        page._ensure_clicked_selected(idx_b)
        assert page.selected_paths() == [str(ws / "b.txt")]


class TestDelete:
    """删除入回收站."""

    def test_delete_confirm_yes(self, page, monkeypatch, qtbot):
        """确认后文件进回收站目录并留痕."""
        page, ctl, ws = page
        (ws / "del.txt").write_text("x")
        idx = page.file_model.index(str(ws / "del.txt"))
        qtbot.waitUntil(idx.isValid)
        page.file_view.setCurrentIndex(idx)
        fake = _FakeBox(QMessageBox.StandardButton.Yes)
        monkeypatch.setattr("shuimu.gui.file_page.QMessageBox", fake)
        page._delete_selected()
        qtbot.waitUntil(lambda: not (ws / "del.txt").exists())
        assert (ws.parent / "trash" / "files" / "del.txt").exists()
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_delete"
        assert json.loads(op["detail"])["items"][0]["src"] == str(ws / "del.txt")

    def test_delete_confirm_no(self, page, monkeypatch, qtbot):
        """取消删除磁盘不动且不留痕."""
        page, ctl, ws = page
        f = ws / "keep.txt"
        f.write_text("x")
        idx = page.file_model.index(str(f))
        qtbot.waitUntil(idx.isValid)
        page.file_view.setCurrentIndex(idx)
        fake = _FakeBox(QMessageBox.StandardButton.No)
        monkeypatch.setattr("shuimu.gui.file_page.QMessageBox", fake)
        page._delete_selected()
        assert f.exists()
        assert not ctl.storage.list_operations()


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


class TestRenameAndMkdir:
    """重命名与新建文件夹."""

    def test_rename(self, page, monkeypatch, qtbot):
        """对话框改名走 controller 留痕."""
        page, ctl, ws = page
        (ws / "a.txt").write_text("x")
        idx = page.file_model.index(str(ws / "a.txt"))
        qtbot.waitUntil(idx.isValid)
        page.file_view.setCurrentIndex(idx)
        monkeypatch.setattr("shuimu.gui.file_page.QInputDialog",
                            _FakeInput("b.txt"))
        page._rename_current()
        qtbot.waitUntil((ws / "b.txt").exists)
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_rename"

    def test_rename_same_name_skipped(self, page, monkeypatch, qtbot):
        """名称未变化不调用 controller."""
        page, ctl, ws = page
        (ws / "a.txt").write_text("x")
        idx = page.file_model.index(str(ws / "a.txt"))
        qtbot.waitUntil(idx.isValid)
        page.file_view.setCurrentIndex(idx)
        monkeypatch.setattr("shuimu.gui.file_page.QInputDialog",
                            _FakeInput("a.txt"))
        page._rename_current()
        assert (ws / "a.txt").exists()
        assert not ctl.storage.list_operations()

    def test_mkdir_default_and_conflict(self, page, monkeypatch, qtbot):
        """新建文件夹用预填名, 重名自动 _1."""
        page, _ctl, ws = page
        monkeypatch.setattr("shuimu.gui.file_page.QInputDialog",
                            _FakeInput("新建文件夹"))
        page._mkdir()
        qtbot.waitUntil((ws / "新建文件夹").is_dir)
        page._mkdir()
        qtbot.waitUntil((ws / "新建文件夹_1").is_dir)


class TestActionsAndMenu:
    """快捷键挂载与右键菜单构造."""

    def test_shortcuts_registered(self, page):
        """六个快捷键挂在视图上, 触发等价于对应槽."""
        page, _ctl, _ws = page
        acts = {act.shortcut().toString(): act
                for act in page.file_view.actions() if not act.shortcut().isEmpty()}
        # Delete 键的 QKeySequence 显示名为 "Del"
        assert set(acts) == {"Ctrl+C", "Ctrl+X", "Ctrl+V",
                             "Del", "F2", "Ctrl+A"}
        assert acts["Ctrl+A"] is page.act_select_all

    def test_menu_on_item(self, page):
        """文件条目菜单含 打开/加入课程/复制/剪切/重命名/删除, 无粘贴."""
        page, _ctl, _ws = page
        texts = [act.text() for act in page._build_context_menu(True).actions()]
        for label in ("打开", "加入课程", "复制", "剪切", "重命名", "删除"):
            assert label in texts
        assert "粘贴" not in texts

    def test_menu_on_dir_item(self, page):
        """目录条目菜单额外含 粘贴."""
        page, _ctl, _ws = page
        texts = [act.text() for act in page._build_context_menu(True, True).actions()]
        for label in ("打开", "复制", "剪切", "粘贴", "重命名", "删除"):
            assert label in texts

    def test_menu_on_blank(self, page):
        """空白菜单含 粘贴/新建文件夹/全选, 粘贴态随剪贴板."""
        page, _ctl, _ws = page
        menu = page._build_context_menu(False)
        texts = [act.text() for act in menu.actions()]
        for label in ("粘贴", "新建文件夹", "全选"):
            assert label in texts
        assert not page.act_paste.isEnabled()
        page._clipboard = {"mode": "copy", "paths": ["/tmp/x"]}
        menu = page._build_context_menu(False)
        assert page.act_paste.isEnabled()
