"""中栏文件浏览页: 工作目录定位 + QFileSystemModel 直读磁盘.

浏览不等于登记: 选中后点"加入选中课程"经 add_requested 信号交主窗口入库;
双击文件经 open_requested 信号交主窗口用系统默认程序打开
(打开分发逻辑集中在 main_window 一处, 便于测试替换).
"""

import os

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

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

    def __init__(self, workspace_root: str = None, parent=None):
        super().__init__(parent)
        self._workspace_root = workspace_root or default_workspace_root()
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
        self.file_view.doubleClicked.connect(self._on_double_clicked)
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
