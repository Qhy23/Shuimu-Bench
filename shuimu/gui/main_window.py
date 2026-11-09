"""主窗口与三栏 UI.

左栏: 课程空间树(SQLite 索引) | 中栏: 工作区页签(文件/链接) |
右栏: 整理/日程面板; 操作留痕经标题栏按钮弹出预览.
浏览不等于登记: 中栏选中后点"加入课程"才入索引; 整理先预览再执行.
"""

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QBrush, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizeGrip,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shuimu import APP_VERSION
from shuimu.core import url_open
from shuimu.core.config import font_size_value
from shuimu.core.controller import KIND_LABEL, Controller
from shuimu.core.storage import Storage
from shuimu.core.url_open import wslview_open as _wslview_open
from shuimu.gui import artwork, theme
from shuimu.gui.dialogs import LogPreviewDialog, warn
from shuimu.gui.file_page import FileBrowserPage
from shuimu.gui.links_page import LinksPage
from shuimu.gui.plugin_runner import run_plugin_dialogs
from shuimu.gui.schedule_view import ScheduleTab
from shuimu.gui.titlebar import TitleBar

ROLE_RESOURCE_ID = Qt.UserRole
ROLE_COURSE_ID = Qt.UserRole + 1


def tree_icon_size() -> int:
    """课程树节点图标边长, 随全局字号."""
    return max(12, round(theme.current_font_px() * 1.25))


def _button(text: str, slot) -> QPushButton:
    """创建按钮并连接点击回调."""
    btn: QPushButton = QPushButton(text)
    btn.clicked.connect(slot)
    return btn


def _open_local(path: str) -> bool:
    """用系统默认程序打开本地文件或目录(独立成函数便于测试替换)."""
    via_wsl = _wslview_open(path)
    if via_wsl is not None:
        return via_wsl
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _open_url(url: str) -> bool:
    """用系统浏览器打开链接(独立成函数便于测试替换)."""
    via_wsl = _wslview_open(url)
    if via_wsl is not None:
        return via_wsl
    return QDesktopServices.openUrl(QUrl(url))


# URL 分发层的默认处理器在此注册: 链接资源与链接页统一经 url_open.open_url
# 打开, 未来浏览器插件可注册内嵌方式与之并存(见 core/url_open.py).
# _wslview_open 为 core 实现的模块内别名(WSL 分发与测试替换入口).
url_open.set_default(_open_url)


class MainWindow(QMainWindow):
    """三栏主窗口(无边框, 自绘标题栏)."""

    def __init__(self, storage: Storage, schedule_path: str = None,
                 links_path: str = None):
        super().__init__()
        self.storage = storage
        self.ctl = Controller(storage, schedule_path=schedule_path,
                              links_path=links_path)
        self._plan = []
        self.dir_edit = None
        self.plan_table = None
        self._log_dialog = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        container = QWidget(self)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        self.title_bar = TitleBar(self, self.ctl.config["app"]["title"],
                                  on_about=self._show_about,
                                  on_log=self._open_log_dialog)
        self.setWindowIcon(QIcon(artwork.bench_icon(256)))
        container_layout.addWidget(self.title_bar)

        self.splitter = QSplitter(container)
        container_layout.addWidget(self.splitter)
        self.setCentralWidget(container)
        self._build_left()
        self._build_center()
        self._build_right()
        self.splitter.setSizes(self.ctl.config["app"]["splitter_sizes"])
        self._install_zoom_shortcuts()

        grip = QSizeGrip(self)
        grip.setStyleSheet("background: transparent;")
        self.statusBar().addPermanentWidget(grip)

        self.reload_courses()
        self.statusBar().showMessage("就绪")

    def _show_about(self) -> None:
        """关于对话框: logo + 版本 + 课程信息."""
        box = QMessageBox(self)
        box.setWindowTitle("关于")
        box.setIconPixmap(artwork.bench_icon(theme.current_font_px() * 5))
        box.setText(f"水木工作台 ShuimuBench v{APP_VERSION}")
        box.setInformativeText(
            "本地优先的课程学习工作台\n"
            "清华大学《AI 编程进阶: 方法与实践》课程项目")
        box.exec()

    # ---- 界面缩放 ----

    def _install_zoom_shortcuts(self) -> None:
        """注册界面缩放快捷键(Ctrl+= / Ctrl+- / Ctrl+0)."""
        for seq in ("Ctrl+=", "Ctrl++"):
            QShortcut(QKeySequence(seq), self, activated=self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self._zoom_reset)

    def _zoom_in(self) -> None:
        """放大一档."""
        self._zoom(1)

    def _zoom_out(self) -> None:
        """缩小一档."""
        self._zoom(-1)

    def _zoom(self, delta: int) -> None:
        """按步进缩放, 到边界提示且不重刷."""
        target = theme.clamp_font_px(theme.current_font_px() + delta)
        if target == theme.current_font_px():
            edge = "最大" if delta > 0 else "最小"
            self.statusBar().showMessage(f"已达{edge}字号 {target}px")
            return
        self.set_app_font(target)

    def _zoom_reset(self) -> None:
        """重置到 config 配置的默认字号."""
        self.set_app_font(font_size_value(self.ctl.config))
        self.statusBar().showMessage(f"字号已重置为 {theme.current_font_px()}px")

    def set_app_font(self, font_px: int, quiet: bool = False) -> None:
        """应用全局字号: 刷新 QSS 并联动自绘部件."""
        theme.apply_theme(QApplication.instance(), font_px)
        self.schedule_tab.set_font_px(theme.current_font_px())
        self.links_page.set_font_px(theme.current_font_px())
        self.title_bar.set_font_px(theme.current_font_px())
        self.reload_courses()  # 树节点图标随字号重建
        if not quiet:
            self.statusBar().showMessage(
                f"字号 {theme.current_font_px()}px (Ctrl+0 重置)")

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
        self.center_tabs = QTabWidget(self.splitter)
        self.file_page = FileBrowserPage(self.ctl, parent=self.center_tabs)
        self.file_page.add_requested.connect(self._on_add_requested)
        self.file_page.open_requested.connect(self._on_open_requested)
        self.file_page.status_message.connect(self.statusBar().showMessage)
        self.file_page.set_plugin_catalog(self.ctl.plugins.catalog())
        self.file_page.plugin_requested.connect(self._on_plugin_requested)
        self.center_tabs.addTab(self.file_page, "文件")
        self.links_page = LinksPage(self.ctl, self.center_tabs)
        self.center_tabs.addTab(self.links_page, "链接")
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)

    def _on_center_tab_changed(self, index: int) -> None:
        """切到链接页时刷新列表(兜底 data/links.yaml 被外部编辑)."""
        if self.center_tabs.widget(index) is self.links_page:
            self.links_page.reload()

    @property
    def workspace_root(self) -> str:
        """中栏工作目录(转发文件页)."""
        return self.file_page.workspace_root

    def set_workspace_root(self, path: str) -> bool:
        """调整中栏工作目录(转发文件页)."""
        return self.file_page.set_workspace_root(path)

    def _on_add_requested(self, path: str) -> None:
        """文件页请求把选中项登记进当前课程."""
        course_id = self._current_course_id()
        if course_id is None:
            self.statusBar().showMessage("请先在左栏选中一门课程")
            return
        result = self.ctl.add_resource(course_id, path)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()

    def _on_open_requested(self, path: str) -> None:
        """文件页请求用系统默认程序打开文件."""
        if not _open_local(path):
            self.statusBar().showMessage(f"无法打开: {path}")

    def _on_plugin_requested(self, plugin_id: str, paths: list) -> None:
        """文件页请求运行插件: 参数对话框 -> 运行 -> 结果对话框."""
        record = self.ctl.plugins.get(plugin_id)
        if record is None or record.status != "ok":
            self.statusBar().showMessage(f"插件不可用: {plugin_id}")
            return
        result = run_plugin_dialogs(self, self.ctl, record, paths)
        self.statusBar().showMessage(result.message)

    def _open_log_dialog(self) -> None:
        """打开操作留痕预览(非模态, 可常开对照)."""
        if self._log_dialog is None:
            self._log_dialog = LogPreviewDialog(self.storage, self)
            self._log_dialog.finished.connect(self._on_log_dialog_closed)
        self._log_dialog.refresh()
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    def _on_log_dialog_closed(self) -> None:
        """预览关闭后清引用, 下次点击重建."""
        self._log_dialog = None

    def _build_right(self) -> None:
        self.tabs = QTabWidget(self.splitter)
        self.tabs.addTab(self._build_organize_tab(), "整理")
        self.schedule_tab = ScheduleTab(self.ctl, self.tabs)
        self.tabs.addTab(self.schedule_tab, "日程")

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
            node.setIcon(0, QIcon(artwork.kind_icon(
                "course", size=tree_icon_size())))
            for res in self.storage.list_resources(course["id"]):
                exists, changed = Storage.check_resource(res)
                label = "[" + KIND_LABEL[res["kind"]] + "] " + res["display_name"]
                if not exists:
                    label += " (失效)"
                elif changed:
                    label += " (已变更)"
                item = QTreeWidgetItem(node, [label])
                item.setData(0, ROLE_RESOURCE_ID, res["id"])
                item.setIcon(0, QIcon(artwork.kind_icon(
                    res["kind"], failed=not exists,
                    size=tree_icon_size())))
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

    def _on_resource_activated(self, item, _column) -> None:
        """双击资源: 链接用浏览器打开, 文件用系统默认程序打开, 目录在中栏定位."""
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is None:
            return
        res = self.storage.get_resource(rid)
        if res is None:
            return
        path = res["path_or_url"]
        if res["kind"] == "link":
            if not url_open.open_url(path):
                self.statusBar().showMessage(f"无法打开链接: {path}")
            return
        if not os.path.exists(path):
            self.statusBar().showMessage(f"已失效: {path}")
            return
        if os.path.isfile(path):
            if _open_local(path):
                self.statusBar().showMessage(f"已打开: {path}")
            else:
                warn(self, "无法打开文件",
                      f"系统没有可用于打开该文件的默认程序:\n{path}")
            return
        self.set_workspace_root(path)

    # ---- 右栏: 整理 ----

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要整理的目录")
        if path:
            self.dir_edit.setText(path)

    def _use_browser_dir(self) -> None:
        path = self.file_page.current_dir()
        if path is None:
            self.statusBar().showMessage("请先在中栏选中目录")
            return
        self.dir_edit.setText(path)

    def _preview_plan(self) -> None:
        directory = self.dir_edit.text().strip()
        result = self.ctl.preview_organize(directory)
        self.statusBar().showMessage(result.message)
        if not result.ok:
            warn(self, "无法生成预览", result.message + "\n\n请检查目录路径是否正确.")
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
            return
        result = self.ctl.execute_organize(directory, plan)
        self.plan_table.setRowCount(0)
        self._plan = []
        self.statusBar().showMessage(result.message)
