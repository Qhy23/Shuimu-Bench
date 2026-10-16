"""主窗口与三栏 UI.

左栏: 课程空间树(SQLite 索引) | 中栏: 文件浏览(直读磁盘) | 右栏: 日志/整理面板.
浏览不等于登记: 中栏选中后点"加入课程"才入索引; 整理先预览再执行.
"""

import os
import time

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .controller import Controller
from .paths import default_workspace_root
from .storage import Storage

KIND_LABEL = {"file": "[文件]", "dir": "[目录]", "link": "[链接]"}
ROLE_RESOURCE_ID = Qt.UserRole
ROLE_COURSE_ID = Qt.UserRole + 1


def _button(text: str, slot) -> QPushButton:
    """创建按钮并连接点击回调."""
    btn: QPushButton = QPushButton(text)
    btn.clicked.connect(slot)
    return btn


def _warn(parent, title: str, text: str) -> None:
    """弹出警告对话框(独立成函数便于测试替换)."""
    QMessageBox.warning(parent, title, text)


class MainWindow(QMainWindow):
    """三栏主窗口."""

    def __init__(self, storage: Storage):
        super().__init__()
        self.storage = storage
        self.ctl = Controller(storage)
        self._plan = []
        self.dir_edit = None
        self.plan_table = None
        self.root_edit = None
        self.workspace_root = default_workspace_root()

        self.splitter = QSplitter(self)
        self.setCentralWidget(self.splitter)
        self._build_left()
        self._build_center()
        self._build_right()
        self.splitter.setSizes([240, 520, 400])

        self.reload_courses()
        self.refresh_log()
        self.statusBar().showMessage("就绪")

    # ---- 界面构建 ----

    def _build_left(self) -> None:
        panel = QWidget(self.splitter)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.course_tree = QTreeWidget(panel)
        self.course_tree.setHeaderLabel("课程空间")
        self.course_tree.itemDoubleClicked.connect(self._on_resource_activated)
        layout.addWidget(self.course_tree)
        row = QHBoxLayout()
        row.addWidget(_button("新建课程", self._add_course))
        row.addWidget(_button("添加链接", self._add_link))
        layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(_button("删除课程", self._delete_course))
        row2.addWidget(_button("移除选中", self._remove_selected))
        row2.addWidget(_button("刷新", self.reload_courses))
        layout.addLayout(row2)

    def _build_center(self) -> None:
        panel = QWidget(self.splitter)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        # 路径栏: 显示并调整中栏工作目录
        row = QHBoxLayout()
        self.root_edit = QLineEdit(self.workspace_root)
        self.root_edit.setToolTip("中栏工作目录, 回车生效")
        self.root_edit.returnPressed.connect(self._apply_root_edit)
        row.addWidget(self.root_edit)
        row.addWidget(_button("上级", self._go_up))
        row.addWidget(_button("浏览...", self._pick_workspace_root))
        layout.addLayout(row)
        self.file_model = QFileSystemModel(panel)
        self.file_model.setRootPath("")  # 模型挂载整个文件系统, 视图定位到工作目录
        self.file_view = QTreeView(panel)
        self.file_view.setModel(self.file_model)
        self.file_view.setRootIndex(self.file_model.index(self.workspace_root))
        self.file_view.setColumnWidth(0, 260)
        self.file_view.doubleClicked.connect(self._on_browser_double_clicked)
        layout.addWidget(self.file_view)
        layout.addWidget(_button("加入选中课程", self._add_selected_files))

    def set_workspace_root(self, path: str) -> bool:
        """调整中栏工作目录. 成功返回 True 并同步路径栏与视图."""
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if not os.path.isdir(path):
            self.statusBar().showMessage(f"目录不存在: {path}")
            self.root_edit.setText(self.workspace_root)
            _warn(self, "无效的工作目录",
                  f"目录不存在或不可访问:\n{path}\n\n已保持当前工作目录不变.")
            return False
        self.workspace_root = path
        self.root_edit.setText(path)
        self.file_view.setRootIndex(self.file_model.index(path))
        self.statusBar().showMessage(f"工作目录: {path}")
        return True

    def _apply_root_edit(self) -> None:
        self.set_workspace_root(self.root_edit.text())

    def _pick_workspace_root(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择工作目录", self.workspace_root)
        if path:
            self.set_workspace_root(path)

    def _go_up(self) -> None:
        parent = os.path.dirname(self.workspace_root)
        if parent != self.workspace_root:
            self.set_workspace_root(parent)

    def _on_browser_double_clicked(self, index) -> None:
        """双击目录则进入该目录."""
        if self.file_model.isDir(index):
            self.set_workspace_root(self.file_model.filePath(index))

    def _build_right(self) -> None:
        self.tabs = QTabWidget(self.splitter)
        self.log_view = QPlainTextEdit(readOnly=True)
        self.tabs.addTab(self.log_view, "日志")
        self.tabs.addTab(self._build_organize_tab(), "整理")

    def _build_organize_tab(self) -> QWidget:
        tab = QWidget(self.tabs)
        layout = QVBoxLayout(tab)
        hint = QLabel("整理功能: 把指定目录里的散乱文件按类型归类到子目录. "
                      "这里选的是要整理的目录, 与中栏浏览位置互不影响.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.dir_edit = QLineEdit(self.workspace_root)
        row.addWidget(QLabel("目录:"))
        row.addWidget(self.dir_edit)
        row.addWidget(_button("浏览...", self._pick_dir))
        layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(_button("用中栏选中目录", self._use_browser_dir))
        row2.addWidget(_button("生成预览", self._preview_plan))
        row2.addWidget(_button("执行整理", self._execute_plan))
        layout.addLayout(row2)
        self.plan_table = QTableWidget(0, 2)
        self.plan_table.setHorizontalHeaderLabels(["源文件", "目标位置"])
        layout.addWidget(self.plan_table)
        return tab

    # ---- 左栏: 课程与资源索引 ----

    def reload_courses(self) -> None:
        """从索引重建课程树, 失效资源标灰."""
        self.course_tree.clear()
        for course in self.storage.list_courses():
            node = QTreeWidgetItem(self.course_tree, [course["name"]])
            node.setData(0, ROLE_COURSE_ID, course["id"])
            for res in self.storage.list_resources(course["id"]):
                exists, changed = Storage.check_resource(res)
                label = KIND_LABEL[res["kind"]] + " " + res["display_name"]
                if not exists:
                    label += " (失效)"
                elif changed:
                    label += " (已变更)"
                item = QTreeWidgetItem(node, [label])
                item.setData(0, ROLE_RESOURCE_ID, res["id"])
                if not exists:
                    item.setForeground(0, QBrush(Qt.GlobalColor.gray))
            node.setExpanded(True)

    def _current_course_id(self):
        item = self.course_tree.currentItem()
        if item is None:
            return None
        course_id = item.data(0, ROLE_COURSE_ID)
        if course_id is not None:
            return course_id
        parent = item.parent()
        return parent.data(0, ROLE_COURSE_ID) if parent is not None else None

    def _add_course(self) -> None:
        name, ok = QInputDialog.getText(self, "新建课程", "课程名称:")
        if not ok:
            return
        result = self.ctl.add_course(name)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()

    def _add_link(self) -> None:
        course_id = self._current_course_id()
        if course_id is None:
            self.statusBar().showMessage("请先在左栏选中一门课程")
            return
        url, ok = QInputDialog.getText(self, "添加链接", "URL(http/https):")
        if not ok or not url.strip():
            return
        result = self.ctl.add_resource(course_id, url.strip())
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()
            self.refresh_log()

    def _add_selected_files(self) -> None:
        course_id = self._current_course_id()
        if course_id is None:
            self.statusBar().showMessage("请先在左栏选中一门课程")
            return
        index = self.file_view.currentIndex()
        if not index.isValid():
            self.statusBar().showMessage("请先在中栏选中文件或目录")
            return
        result = self.ctl.add_resource(course_id, self.file_model.filePath(index))
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()
            self.refresh_log()

    def _delete_course(self) -> None:
        """删除选中课程: 确认后级联移除其资源索引, 磁盘文件不动."""
        item = self.course_tree.currentItem()
        if item is None:
            self.statusBar().showMessage("请先选中课程")
            return
        course_id = item.data(0, ROLE_COURSE_ID)
        if course_id is None:
            course_id = item.parent().data(0, ROLE_COURSE_ID)
        course = self.storage.get_course(course_id)
        if course is None:
            return
        n = len(self.storage.list_resources(course_id))
        answer = QMessageBox.question(
            self, "删除课程",
            f"删除课程「{course['name']}」及其 {n} 条资源索引?\n(磁盘文件不受影响)")
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.ctl.delete_course(course_id)
        self.statusBar().showMessage(result.message)
        self.reload_courses()
        self.refresh_log()

    def _remove_selected(self) -> None:
        item = self.course_tree.currentItem()
        if item is None:
            return
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is None:
            self.statusBar().showMessage("选中的是课程节点, 请用「删除课程」按钮")
            return
        res = self.storage.get_resource(rid)
        if res is None:
            return
        answer = QMessageBox.question(
            self, "移除资源", f"从索引移除(不动磁盘文件):\n{res['path_or_url']}?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.ctl.remove_resource(rid)
        self.statusBar().showMessage(result.message)
        self.reload_courses()
        self.refresh_log()

    def _on_resource_activated(self, item, _column) -> None:
        """双击资源: 链接用浏览器打开; 文件/目录在中栏定位."""
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is None:
            return
        res = self.storage.get_resource(rid)
        if res is None:
            return
        path = res["path_or_url"]
        if res["kind"] == "link":
            QDesktopServices.openUrl(QUrl(path))
            return
        if not os.path.exists(path):
            self.statusBar().showMessage(f"已失效: {path}")
            return
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self.set_workspace_root(path)

    # ---- 右栏: 日志 ----

    def refresh_log(self) -> None:
        """从 operations 表重建日志视图."""
        lines = []
        for op in self.storage.list_operations():
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(op["started_at"]))
            parts = [f"[{ts}]", op["status"], op["action"]]
            if op["path_snapshot"]:
                parts.append(op["path_snapshot"])
            if op["detail"]:
                parts.append(str(op["detail"]))
            lines.append(" ".join(parts))
        self.log_view.setPlainText("\n".join(lines) or "(暂无操作记录)")

    # ---- 右栏: 整理 ----

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要整理的目录")
        if path:
            self.dir_edit.setText(path)

    def _use_browser_dir(self) -> None:
        index = self.file_view.currentIndex()
        if not index.isValid():
            self.statusBar().showMessage("请先在中栏选中目录")
            return
        path = self.file_model.filePath(index)
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self.dir_edit.setText(path)

    def _preview_plan(self) -> None:
        directory = self.dir_edit.text().strip()
        result = self.ctl.preview_organize(directory)
        self.statusBar().showMessage(result.message)
        if not result.ok:
            _warn(self, "无法生成预览", result.message + "\n\n请检查目录路径是否正确.")
            return
        self._plan = result.detail["plan"]
        self.plan_table.setRowCount(len(self._plan))
        for row, item in enumerate(self._plan):
            self.plan_table.setItem(row, 0, QTableWidgetItem(item.src))
            self.plan_table.setItem(row, 1, QTableWidgetItem(item.dst))

    def _execute_plan(self) -> None:
        plan = self._plan
        directory = self.dir_edit.text().strip()
        if not plan:
            self.statusBar().showMessage("请先生成预览")
            return
        answer = QMessageBox.question(
            self, "执行整理", f"将移动 {len(plan)} 个文件, 确认执行?")
        if answer != QMessageBox.StandardButton.Yes:
            self.ctl.cancel_organize(directory)
            self.refresh_log()
            return
        result = self.ctl.execute_organize(directory, plan)
        self.plan_table.setRowCount(0)
        self._plan = []
        self.refresh_log()
        self.statusBar().showMessage(result.message)
