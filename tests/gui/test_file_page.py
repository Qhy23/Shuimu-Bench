"""file_page.py 单元测试: 文件页资源管理器操作(复制/剪切/粘贴/删除/重命名/新建).

断言一律落在磁盘事实(文件存在性)而非视图行数: QFileSystemModel 的
文件系统监听是异步的. 多数对话框经 monkeypatch 替换模块内引用;
同名冲突框另有定时器驱动的真实弹框测试(_drive_conflict_dialogs).
"""

import json
import os
from copy import deepcopy

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox

from shuimu.core.config import DEFAULTS
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage
from shuimu.gui.file_page import FileBrowserPage
from shuimu.gui import file_page as shuimu_file_page


def _norm(*paths):
    """路径串列表统一分隔符后比较(Qt 惯用 /, Windows 下 os.path 拼为 \\)."""
    return [os.path.normpath(p) for p in paths]


@pytest.fixture()
def page(tmp_path, monkeypatch, qtbot):
    """带独立 controller 与回收站目录的文件页(workspace 指向临时目录)."""
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
    """文件路径选择、图标展示与多选读取."""

    def test_file_browser_uses_type_icon_for_files(self, qtbot, tmp_path,
                                                   monkeypatch):
        """中栏文件模型对文件走后缀图标, 目录保持默认."""
        page = FileBrowserPage(workspace_root=str(tmp_path))
        qtbot.addWidget(page)
        path = tmp_path / "demo.pdf"
        path.write_text("x", encoding="utf-8")
        directory = tmp_path / "folder"
        directory.mkdir()
        calls = []
        original = shuimu_file_page.file_icons.file_type_icon

        def record(file_path, **kwargs):
            calls.append(file_path)
            return original(file_path, **kwargs)

        monkeypatch.setattr(shuimu_file_page.file_icons, "file_type_icon", record)
        index = page.file_model.index(str(path))
        assert index.isValid()
        assert not page.file_model.data(index, Qt.ItemDataRole.DecorationRole).isNull()
        assert os.path.normpath(str(path)) in _norm(*calls)
        calls.clear()
        for column in (1, 2, 3):
            other = index.sibling(index.row(), column)
            page.file_model.data(other, Qt.ItemDataRole.DecorationRole)
        assert not calls  # Size/Type/Modified 列不添加文件图标
        directory_index = page.file_model.index(str(directory))
        calls.clear()
        page.file_model.data(directory_index, Qt.ItemDataRole.DecorationRole)
        assert not calls

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
        assert _norm(*page.selected_paths()) == _norm(str(ws / "a.txt"), str(ws / "b.txt"))
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

    def test_copy_then_paste_twice(self, page, monkeypatch, qtbot):
        """复制可多次粘贴; 二次粘贴同名冲突选保留两者得 _1, 剪贴板保留."""
        page, _ctl, ws = page
        other = ws.parent / "other"
        other.mkdir()
        (other / "a.txt").write_text("x")
        page._clipboard = {"mode": "copy", "paths": [str(other / "a.txt")]}
        page._paste()
        qtbot.waitUntil((ws / "a.txt").exists)
        monkeypatch.setattr(page, "_ask_conflict",
                            lambda *_a, **_k: ("keep_both", False))
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
        assert _norm(page._paste_target()) == _norm(str(sub))
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
        assert _norm(page._paste_target()) == _norm(str(ws))
        # 选中单个文件 -> 工作目录(文件不是粘贴目标)
        sel.select(idx_f, QItemSelectionModel.SelectionFlag.ClearAndSelect
                   | QItemSelectionModel.SelectionFlag.Rows)
        assert _norm(page._paste_target()) == _norm(str(ws))
        # 文件与目录多选 -> 工作目录
        sel.select(idx_s, QItemSelectionModel.SelectionFlag.Select
                   | QItemSelectionModel.SelectionFlag.Rows)
        assert _norm(page._paste_target()) == _norm(str(ws))
        # 恰好一个目录 -> 该目录
        sel.select(idx_f, QItemSelectionModel.SelectionFlag.Deselect
                   | QItemSelectionModel.SelectionFlag.Rows)
        assert _norm(page._paste_target()) == _norm(str(sub))

    def test_right_click_switches_selection(self, page, qtbot):
        """右键未选中的条目时, 选择切到该条目."""
        page, _ctl, ws = page
        for name in ("a.txt", "b.txt"):
            (ws / name).write_text(name)
        idx_a = page.file_model.index(str(ws / "a.txt"))
        idx_b = page.file_model.index(str(ws / "b.txt"))
        qtbot.waitUntil(lambda: idx_a.isValid() and idx_b.isValid())
        page.file_view.setCurrentIndex(idx_a)
        assert _norm(*page.selected_paths()) == _norm(str(ws / "a.txt"))
        page._ensure_clicked_selected(idx_b)
        assert _norm(*page.selected_paths()) == _norm(str(ws / "b.txt"))
        # 已在选中集内的条目不重置多选
        page._ensure_clicked_selected(idx_b)
        assert _norm(*page.selected_paths()) == _norm(str(ws / "b.txt"))


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
        assert _norm(json.loads(op["detail"])["items"][0]["src"]) == _norm(str(ws / "del.txt"))

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
        """七个快捷键挂在视图上, 触发等价于对应槽."""
        page, _ctl, _ws = page
        acts = {act.shortcut().toString(): act
                for act in page.file_view.actions() if not act.shortcut().isEmpty()}
        # Delete 键的 QKeySequence 显示名为 "Del"
        assert set(acts) == {"Ctrl+C", "Ctrl+X", "Ctrl+V",
                             "Del", "F2", "Ctrl+A", "Ctrl+Z"}
        assert acts["Ctrl+A"] is page.act_select_all
        assert acts["Ctrl+Z"] is page.act_undo

    def test_undo_via_shortcut(self, page, monkeypatch, qtbot):
        """Ctrl+Z 槽: 新建文件夹后撤销即消失并留痕."""
        page, ctl, ws = page
        monkeypatch.setattr("shuimu.gui.file_page.QInputDialog",
                            _FakeInput("撤销我"))
        page._mkdir()
        qtbot.waitUntil((ws / "撤销我").is_dir)
        page._undo()   # 直接调槽: trigger() 在 offscreen 下为排队投递, 时序不定
        assert not (ws / "撤销我").exists()
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "undo"

    def test_undo_empty_stack_hint(self, page):
        """空撤销栈给提示."""
        page, _ctl, _ws = page
        received = []
        page.status_message.connect(received.append)
        page._undo()
        assert any("没有可撤销" in msg for msg in received)

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
        """空白菜单含 粘贴/新建文件夹/撤销/全选, 粘贴态随剪贴板."""
        page, _ctl, _ws = page
        menu = page._build_context_menu(False)
        texts = [act.text() for act in menu.actions()]
        for label in ("粘贴", "新建文件夹", "撤销", "全选"):
            assert label in texts
        assert not page.act_paste.isEnabled()
        page._clipboard = {"mode": "copy", "paths": ["/tmp/x"]}
        menu = page._build_context_menu(False)
        assert page.act_paste.isEnabled()


def _drive_conflict_dialogs(button_text: str, times: int = 1,
                            check_first: bool = False) -> None:
    """定时器驱动真实同名冲突框: 弹出一个点一个, 共 times 个.

    须在调用 _ask_conflict/_paste 之前挂上; offscreen 下模态 exec 的
    事件循环会处理定时器. check_first 时先勾选首个框的"应用于其余"
    复选框. 找不到目标按钮则关闭对话框兜底(视为取消).
    """
    state = {"left": times, "first": True}

    def drive():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            QTimer.singleShot(20, drive)
            return
        if check_first and state["first"]:
            for child in box.findChildren(QCheckBox):
                child.click()
        state["first"] = False
        for btn in box.buttons():
            if btn.text() == button_text:
                btn.click()
                break
        else:
            box.close()
            return
        state["left"] -= 1
        if state["left"]:
            QTimer.singleShot(20, drive)

    QTimer.singleShot(20, drive)


class TestConflictPaste:
    """粘贴同名冲突: 决策链逻辑经 mock 测试, 弹框路径用真实对话框驱动."""

    @staticmethod
    def _conflict_src(ws, name="a.txt", content="new"):
        """在 ws 外建一个源文件, 返回路径."""
        other = ws.parent / "conflict_src"
        other.mkdir(exist_ok=True)
        f = other / name
        f.write_text(content)
        return str(f)

    def test_replace_moves_old_to_trash(self, page, monkeypatch, qtbot):
        """覆盖: 旧文件入回收站, 新内容落位, 撤销栈两层可回."""
        page, ctl, ws = page
        (ws / "a.txt").write_text("old")
        src = self._conflict_src(ws)
        page._clipboard = {"mode": "copy", "paths": [src]}
        monkeypatch.setattr(page, "_ask_conflict",
                            lambda *_a, **_k: ("replace", False))
        page._paste()
        qtbot.waitUntil(lambda: (ws / "a.txt").read_text() == "new")
        assert (ws.parent / "trash" / "files" / "a.txt").read_text() == "old"
        actions = [op["action"] for op in ctl.storage.list_operations()]
        assert actions[0] == "file_copy"     # 最新在前
        assert "file_delete" in actions      # 旧文件删除留痕

    def test_skip_leaves_target(self, page, monkeypatch):
        """跳过: 目标不动, 源仍在剪贴板."""
        page, _ctl, ws = page
        (ws / "a.txt").write_text("old")
        src = self._conflict_src(ws)
        page._clipboard = {"mode": "copy", "paths": [src]}
        monkeypatch.setattr(page, "_ask_conflict",
                            lambda *_a, **_k: ("skip", False))
        page._paste()
        assert (ws / "a.txt").read_text() == "old"
        assert page._clipboard["paths"] == [src]

    def test_keep_both_renames(self, page, monkeypatch, qtbot):
        """保留两者: 新文件自动 _1."""
        page, _ctl, ws = page
        (ws / "a.txt").write_text("old")
        src = self._conflict_src(ws)
        page._clipboard = {"mode": "copy", "paths": [src]}
        monkeypatch.setattr(page, "_ask_conflict",
                            lambda *_a, **_k: ("keep_both", False))
        page._paste()
        qtbot.waitUntil((ws / "a_1.txt").exists)
        assert (ws / "a.txt").read_text() == "old"

    def test_cancel_aborts(self, page, monkeypatch):
        """取消: 整个粘贴不执行."""
        page, ctl, ws = page
        (ws / "a.txt").write_text("old")
        src = self._conflict_src(ws)
        page._clipboard = {"mode": "copy", "paths": [src]}
        monkeypatch.setattr(page, "_ask_conflict",
                            lambda *_a, **_k: ("cancel", False))
        page._paste()
        assert (ws / "a.txt").read_text() == "old"
        assert not ctl.storage.list_operations()

    def test_apply_all_covers_rest(self, page, monkeypatch, qtbot):
        """应用于其余全部: 一次决策覆盖全部冲突."""
        page, _ctl, ws = page
        for name in ("a.txt", "b.txt"):
            (ws / name).write_text("old")
        srcs = [self._conflict_src(ws, "a.txt"),
                self._conflict_src(ws, "b.txt")]
        page._clipboard = {"mode": "copy", "paths": srcs}
        asked = []

        def fake_ask(_name, remaining):
            asked.append(remaining)
            return "replace", True    # 首个决策即应用于其余

        monkeypatch.setattr(page, "_ask_conflict", fake_ask)
        page._paste()
        assert asked == [2]           # 只问了第一次, 其余沿决策
        qtbot.waitUntil(lambda: (ws / "b.txt").read_text() == "new")

    def test_resolve_conflicts_flow(self, page, monkeypatch):
        """逐个询问与应用于其余全部的决策链逻辑."""
        page, _ctl, _ws = page
        conflicts = [("/x/a", "/y/a"), ("/x/b", "/y/b"), ("/x/c", "/y/c")]
        answers = iter([("skip", False), ("keep_both", True)])

        def fake_ask(_name, _remaining):
            return next(answers)

        monkeypatch.setattr(page, "_ask_conflict", fake_ask)
        policies = page._resolve_conflicts(conflicts)
        assert policies == {"/x/a": "skip", "/x/b": "keep_both",
                            "/x/c": "keep_both"}

    def test_resolve_conflicts_cancel(self, page, monkeypatch):
        """决策链中取消 -> None."""
        page, _ctl, _ws = page
        monkeypatch.setattr(page, "_ask_conflict", lambda *_a, **_k: ("cancel", False))
        assert page._resolve_conflicts([("/x/a", "/y/a")]) is None

    # ---- 真实弹框 (不 mock _ask_conflict) ----
    # 回归背景 (2026-11-08): _ask_conflict 以临时对象传 QCheckBox 给
    # setCheckBox, 语句结束即被 shiboken6 引用计数回收, 对话框内悬空指针.
    # 全 mock 测试覆盖不到弹框构造路径, 故此组直连真实对话框.

    def test_real_dialog_skip(self, page):
        """真实对话框: 两冲突框点跳过且不勾复选框 -> (skip, False)."""
        page, _ctl, _ws = page
        _drive_conflict_dialogs("跳过")
        assert page._ask_conflict("a.txt", 2) == ("skip", False)

    def test_real_dialog_skip_apply_all(self, page):
        """真实对话框: 勾选"应用于其余"后点跳过 -> (skip, True)."""
        page, _ctl, _ws = page
        _drive_conflict_dialogs("跳过", check_first=True)
        assert page._ask_conflict("a.txt", 2) == ("skip", True)

    def test_real_paste_two_conflicts_all_skip(self, page):
        """端到端真实对话框: 两文件同名冲突连点两次跳过 -> 全跳过不落盘.

        即 2026-11-08 实机崩溃场景的脚本化.
        """
        page, _ctl, ws = page
        for name in ("a.txt", "b.txt"):
            (ws / name).write_text("old")
        srcs = [self._conflict_src(ws, "a.txt"),
                self._conflict_src(ws, "b.txt")]
        page._clipboard = {"mode": "copy", "paths": srcs}
        msgs = []
        page.status_message.connect(msgs.append)
        _drive_conflict_dialogs("跳过", times=2)
        page._paste()
        assert (ws / "a.txt").read_text() == "old"
        assert (ws / "b.txt").read_text() == "old"
        assert page._clipboard["paths"] == srcs
        assert msgs[-1] == "已跳过全部项目"
