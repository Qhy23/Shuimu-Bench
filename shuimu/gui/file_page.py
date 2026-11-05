"""中栏文件浏览页: 工作目录定位 + QFileSystemModel 直读磁盘 + 资源管理器基本操作.

浏览不等于登记: 选中后点"加入选中课程"经 add_requested 信号交主窗口入库;
双击文件经 open_requested 信号交主窗口用系统默认程序打开
(打开分发逻辑集中在 main_window 一处, 便于测试替换).

文件操作(复制/剪切/粘贴/删除/重命名/新建文件夹)经 controller 留痕;
删除为移入回收站(后端分发见 core/trash.py). 剪贴板为应用内部状态,
不碰系统剪贴板. 已知边界: 删除工作目录自身或其祖先时视图行为未定义,
本轮不处理.
"""

import os

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from shuimu.core.controller import Controller
from shuimu.core.fileops import find_conflicts
from shuimu.core.paths import default_workspace_root
from shuimu.gui.dialogs import warn


def _button(text: str, slot) -> QPushButton:
    """创建按钮并连接点击回调."""
    btn: QPushButton = QPushButton(text)
    btn.clicked.connect(slot)
    return btn


class FileBrowserPage(QWidget):
    """文件浏览页(中栏"文件"页签)."""

    add_requested = Signal(str)    # 请求把选中路径登记进当前课程
    open_requested = Signal(str)   # 请求用系统默认程序打开文件
    status_message = Signal(str)   # 状态栏提示(工作目录切换/校验失败等)

    def __init__(self, controller: Controller = None,
                 workspace_root: str = None, parent=None):
        super().__init__(parent)
        self._ctl = controller
        self._workspace_root = workspace_root or default_workspace_root()
        self._clipboard = {"mode": None, "paths": []}  # 应用内部剪贴板
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # 路径栏: 显示并调整工作目录
        row = QHBoxLayout()
        self.root_edit = QLineEdit(self._workspace_root)
        self.root_edit.setToolTip("中栏工作目录, 回车生效")
        self.root_edit.returnPressed.connect(self._apply_root_edit)
        row.addWidget(self.root_edit)
        row.addWidget(_button("上级", self._go_up))
        row.addWidget(_button("浏览...", self._pick_workspace_root))
        layout.addLayout(row)
        self.file_model = QFileSystemModel(self)
        self.file_model.setRootPath("")  # 模型挂载整个文件系统, 视图定位到工作目录
        self.file_view = QTreeView(self)
        self.file_view.setModel(self.file_model)
        self.file_view.setRootIndex(self.file_model.index(self._workspace_root))
        self.file_view.setColumnWidth(0, 260)
        # 多选 + 整行选中; 关闭内联编辑, 重命名一律走对话框+controller 留痕
        self.file_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_view.customContextMenuRequested.connect(self._show_context_menu)
        self.file_view.doubleClicked.connect(self._on_double_clicked)
        self._build_actions()
        layout.addWidget(self.file_view)
        layout.addWidget(_button("加入选中课程", self._request_add))

    @property
    def workspace_root(self) -> str:
        """当前工作目录."""
        return self._workspace_root

    def selected_path(self):
        """当前选中项路径, 无选中返回 None."""
        index = self.file_view.currentIndex()
        if not index.isValid():
            return None
        return self.file_model.filePath(index)

    def selected_paths(self) -> list:
        """全部选中项路径(多选), 无选中返回空列表."""
        return [self.file_model.filePath(index)
                for index in self.file_view.selectionModel().selectedRows(0)]

    def current_dir(self):
        """选中目录; 选中文件时取其所在目录. 无选中返回 None."""
        path = self.selected_path()
        if path is None:
            return None
        if os.path.isfile(path):
            return os.path.dirname(path)
        return path

    def set_workspace_root(self, path: str) -> bool:
        """调整工作目录. 成功返回 True 并同步路径栏与视图."""
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if not os.path.isdir(path):
            self.status_message.emit(f"目录不存在: {path}")
            self.root_edit.setText(self._workspace_root)
            warn(self, "无效的工作目录",
                 f"目录不存在或不可访问:\n{path}\n\n已保持当前工作目录不变.")
            return False
        self._workspace_root = path
        self.root_edit.setText(path)
        self.file_view.setRootIndex(self.file_model.index(path))
        self.status_message.emit(f"工作目录: {path}")
        return True

    def _apply_root_edit(self) -> None:
        self.set_workspace_root(self.root_edit.text())

    def _pick_workspace_root(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择工作目录", self._workspace_root)
        if path:
            self.set_workspace_root(path)

    def _go_up(self) -> None:
        parent = os.path.dirname(self._workspace_root)
        if parent != self._workspace_root:
            self.set_workspace_root(parent)

    def _on_double_clicked(self, index) -> None:
        """双击目录进入该目录, 双击文件交主窗口用系统默认程序打开."""
        if self.file_model.isDir(index):
            self.set_workspace_root(self.file_model.filePath(index))
            return
        self.open_requested.emit(self.file_model.filePath(index))

    def _request_add(self) -> None:
        """请求把选中项登记进课程(课程判定与入库归主窗口)."""
        path = self.selected_path()
        if path is None:
            self.status_message.emit("请先在中栏选中文件或目录")
            return
        self.add_requested.emit(path)

    # ---- 文件操作 ----

    def _build_actions(self) -> None:
        """创建操作 QAction 挂到视图(快捷键仅焦点在树内时生效).

        WidgetWithChildrenShortcut: 不抢路径栏输入框的原生 Ctrl+C/V/A,
        也与主窗口 Ctrl+=/-/0 缩放键无重叠.
        """

        def make(text: str, slot, key: str = None) -> QAction:
            act = QAction(text, self)
            if key:
                act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.file_view.addAction(act)
            return act

        self.act_copy = make("复制", self._copy_selected, "Ctrl+C")
        self.act_cut = make("剪切", self._cut_selected, "Ctrl+X")
        self.act_paste = make("粘贴", self._paste, "Ctrl+V")
        self.act_delete = make("删除", self._delete_selected, "Delete")
        self.act_rename = make("重命名", self._rename_current, "F2")
        self.act_select_all = make("全选", self._select_all, "Ctrl+A")
        self.act_mkdir = make("新建文件夹", self._mkdir)
        self.act_undo = make("撤销", self._undo, "Ctrl+Z")

    def _show_context_menu(self, pos) -> None:
        index = self.file_view.indexAt(pos)
        self._ensure_clicked_selected(index)
        on_dir = index.isValid() and self.file_model.isDir(index)
        menu = self._build_context_menu(index.isValid(), on_dir)
        menu.exec(self.file_view.viewport().mapToGlobal(pos))

    def _ensure_clicked_selected(self, index) -> None:
        """右键的条目未在选中集内时, 把选择切到该条目.

        否则菜单动作会作用于旧的选中项而非用户所见项.
        """
        if index.isValid() and not self.file_view.selectionModel().isSelected(index):
            self.file_view.setCurrentIndex(index)
            self.file_view.selectionModel().select(
                index, QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows)

    def _build_context_menu(self, on_item: bool, on_dir: bool = False) -> QMenu:
        """构造右键菜单(与弹出分离, 便于测试).

        on_item 为在条目上右键; on_dir 表示该条目是目录(菜单加粘贴).
        空白处只给粘贴/新建/全选.
        """
        menu = QMenu(self)
        if on_item:
            menu.addAction("打开", self._open_current)
            menu.addAction("加入课程", self._request_add)
            menu.addSeparator()
            menu.addAction(self.act_copy)
            menu.addAction(self.act_cut)
            if on_dir:
                menu.addAction(self.act_paste)
            menu.addSeparator()
            menu.addAction(self.act_rename)
            menu.addAction(self.act_delete)
        else:
            menu.addAction(self.act_paste)
            menu.addAction(self.act_mkdir)
            menu.addSeparator()
            menu.addAction(self.act_undo)
            menu.addAction(self.act_select_all)
        self.act_paste.setEnabled(bool(self._clipboard["paths"]))
        return menu

    def _open_current(self) -> None:
        """对当前项执行与双击一致的行为."""
        index = self.file_view.currentIndex()
        if index.isValid():
            self._on_double_clicked(index)

    def _ctl_or_hint(self):
        """取 controller; 未注入时状态栏提示(浏览功能不受影响)."""
        if self._ctl is None:
            self.status_message.emit("控制器不可用, 无法执行文件操作")
            return None
        return self._ctl

    def _selected_or_hint(self) -> list:
        """取全部选中路径; 空选时状态栏提示."""
        paths = self.selected_paths()
        if not paths:
            self.status_message.emit("请先选中文件或目录")
        return paths

    def _copy_selected(self) -> None:
        paths = self._selected_or_hint()
        if not paths:
            return
        self._clipboard = {"mode": "copy", "paths": paths}
        self.status_message.emit(f"已复制 {len(paths)} 项")

    def _cut_selected(self) -> None:
        paths = self._selected_or_hint()
        if not paths:
            return
        self._clipboard = {"mode": "cut", "paths": paths}
        self.status_message.emit(f"已剪切 {len(paths)} 项(粘贴后生效)")

    def _paste(self) -> None:
        """粘贴到目标目录(Explorer 语义).

        恰好选中一个目录时贴入该目录(树形视图展开子目录浏览时,
        工作目录并未跟着变, 须以选中为准), 否则贴进当前工作目录.
        同名冲突逐个询问(见 _resolve_conflicts); "覆盖"先把旧文件移入
        回收站再粘贴. 剪切分支只移出成功的项(部分失败可重试);
        复制分支保留剪贴板可多次粘贴.
        """
        if not self._clipboard["paths"]:
            self.status_message.emit("剪贴板为空")
            return
        ctl = self._ctl_or_hint()
        if ctl is None:
            return
        target = self._paste_target()
        paths = self._clipboard["paths"]
        policies = {}
        conflicts = find_conflicts(paths, target)
        if conflicts:
            policies = self._resolve_conflicts(conflicts)
            if policies is None:
                self.status_message.emit("已取消粘贴")
                return
        skip_srcs = {src for src, policy in policies.items()
                     if policy == "skip"}
        replace_dsts = [dst for src, dst in conflicts
                        if policies.get(src) == "replace"]
        if replace_dsts:
            ctl.file_delete(replace_dsts)  # 旧文件入回收站, 留痕并入撤销栈
        todo = [p for p in paths if p not in skip_srcs]
        if not todo:
            self.status_message.emit("已跳过全部项目")
            return
        action = (ctl.file_copy if self._clipboard["mode"] == "copy"
                  else ctl.file_move)
        result = action(todo, target)
        self.status_message.emit(result.message)
        if self._clipboard["mode"] == "cut":
            done = {item["src"] for item in result.detail.get("items", [])
                    if item["status"] == "ok"}
            remaining = [p for p in self._clipboard["paths"] if p not in done]
            self._clipboard = {"mode": "cut" if remaining else None,
                               "paths": remaining}

    def _paste_target(self) -> str:
        """粘贴目标: 恰好选中一个目录时该目录, 否则当前工作目录."""
        paths = self.selected_paths()
        if len(paths) == 1 and os.path.isdir(paths[0]):
            return paths[0]
        return self._workspace_root

    def _resolve_conflicts(self, conflicts: list):
        """逐个询问同名冲突的处理方式.

        返回 {src: "replace"|"skip"|"keep_both"}, 取消整个粘贴返回 None;
        勾选"应用于其余全部"后其余冲突沿用当前决策.
        """
        policies = {}
        apply_all = None
        for i, (src, _dst) in enumerate(conflicts):
            if apply_all is None:
                choice, apply = self._ask_conflict(
                    os.path.basename(src), len(conflicts) - i)
                if choice == "cancel":
                    return None
                policies[src] = choice
                if apply:
                    apply_all = choice
            else:
                policies[src] = apply_all
        return policies

    def _ask_conflict(self, name: str, remaining: int) -> tuple:
        """单个同名冲突决策框. 返回 (决策, 是否应用于其余全部)."""
        box = QMessageBox(self)
        box.setWindowTitle("同名冲突")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"“{name}” 在目标文件夹已存在。")
        box.setInformativeText("选择如何处理此项。")
        if remaining > 1:
            box.setCheckBox(QCheckBox(f"应用于其余 {remaining - 1} 个冲突"))
        btn_replace = box.addButton("覆盖(旧的移入回收站)",
                                    QMessageBox.ButtonRole.YesRole)
        btn_skip = box.addButton("跳过", QMessageBox.ButtonRole.NoRole)
        btn_keep = box.addButton("两者都保留(自动加 _1)",
                                 QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(btn_keep)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_replace:
            choice = "replace"
        elif clicked is btn_skip:
            choice = "skip"
        elif clicked is btn_keep:
            choice = "keep_both"
        else:
            choice = "cancel"   # Esc 或关闭框
        apply_all = bool(box.checkBox() and box.checkBox().isChecked())
        return choice, apply_all

    def _undo(self) -> None:
        """Ctrl+Z: 撤销最近一次文件页操作."""
        ctl = self._ctl_or_hint()
        if ctl is None:
            return
        result = ctl.undo()
        self.status_message.emit(result.message)

    def _delete_selected(self) -> None:
        paths = self._selected_or_hint()
        if not paths:
            return
        ctl = self._ctl_or_hint()
        if ctl is None:
            return
        names = [os.path.basename(p) for p in paths]
        preview = "\n".join(names[:5]) + ("\n..." if len(names) > 5 else "")
        answer = QMessageBox.question(
            self, "删除",
            f"把选中的 {len(paths)} 个项目移入回收站?\n\n{preview}\n\n可从回收站找回.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = ctl.file_delete(paths)
        self.status_message.emit(result.message)

    def _rename_current(self) -> None:
        path = self.selected_path()
        if path is None:
            self.status_message.emit("请先选中要重命名的项目")
            return
        ctl = self._ctl_or_hint()
        if ctl is None:
            return
        name, ok = QInputDialog.getText(self, "重命名", "新名称:",
                                        text=os.path.basename(path))
        if not ok:
            return
        if not name.strip() or name.strip() == os.path.basename(path):
            self.status_message.emit("名称未变化")
            return
        result = ctl.file_rename(path, name)
        self.status_message.emit(result.message)

    def _mkdir(self) -> None:
        ctl = self._ctl_or_hint()
        if ctl is None:
            return
        name, ok = QInputDialog.getText(self, "新建文件夹", "名称:",
                                        text="新建文件夹")
        if not ok:
            return
        result = ctl.file_mkdir(self._workspace_root, name)
        self.status_message.emit(result.message)

    def _select_all(self) -> None:
        self.file_view.selectAll()
