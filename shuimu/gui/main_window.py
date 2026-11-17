"""主窗口与三栏 UI.

左栏: 课程/插件页签(课程空间树 + 插件面板) | 中栏: 工作区页签(文件/链接) |
右栏: 整理/日程面板; 操作留痕经标题栏按钮弹出预览.
浏览不等于登记: 中栏选中后点"加入课程"才入索引; 整理先预览再执行.
"""

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QBrush,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
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
from shuimu.gui import artwork, dialogs, theme
from shuimu.gui.dialogs import LogPreviewDialog, confirm_network_enable, warn
from shuimu.gui.file_page import FileBrowserPage
from shuimu.gui.links_page import LinksPage
from shuimu.plugins.campus.campus_tab import CampusTab
from shuimu.plugins.learn.learn_page import LearnPage
from shuimu.plugins.cloud.cloud_page import CloudPage
from shuimu.gui.plugin_runner import run_plugin_dialogs
from shuimu.gui.schedule_view import ScheduleTab
from shuimu.gui.titlebar import TitleBar

ROLE_RESOURCE_ID = Qt.UserRole
ROLE_COURSE_ID = Qt.UserRole + 1
ROLE_FOLDER_ID = Qt.UserRole + 2


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
        # 课程树展开状态记忆(会话内, 键见 _tree_state_key); 重建树后恢复
        self._tree_expanded: dict = {}
        # 应用内部剪贴板(与文件页共享, 左栏/中栏互通; 原地变异不重绑)
        self._app_clipboard = {"mode": None, "paths": []}

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
        self._build_course_actions()   # 自 __init__ 直调, 动作挂 course_tree
        self._build_center()
        self._build_right()
        self.splitter.setSizes(self.ctl.config["app"]["splitter_sizes"])
        self._install_zoom_shortcuts()
        # 服务型插件页签注册: (插件 id, 页签容器, 页签部件)
        self._plugin_tabs = (
            ("learn", self.center_tabs, self.learn_page),
            ("cloud", self.center_tabs, self.cloud_page),
            ("campus", self.tabs, self.campus_tab),
        )
        self.reload_plugins()
        self._apply_plugin_tabs()

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
        """左栏与中/右栏同款上部页签: 课程空间 / 插件."""
        self.left_tabs = QTabWidget(self.splitter)
        course_panel = QWidget(self.left_tabs)
        layout = QVBoxLayout(course_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.course_tree = QTreeWidget(course_panel)
        self.course_tree.setHeaderLabel("课程空间")
        self.course_tree.itemDoubleClicked.connect(self._on_resource_activated)
        self.course_tree.itemExpanded.connect(
            lambda item: self._remember_tree_state(item, True))
        self.course_tree.itemCollapsed.connect(
            lambda item: self._remember_tree_state(item, False))
        # 索引管理: 右键菜单 + 快捷键(仅索引层操作, 不动磁盘)
        self.course_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.course_tree.customContextMenuRequested.connect(
            self._show_course_menu)
        layout.addWidget(self.course_tree)
        self.left_tabs.addTab(course_panel, "课程")
        plugin_panel = QWidget(self.left_tabs)
        plugin_layout = QVBoxLayout(plugin_panel)
        plugin_layout.setContentsMargins(0, 0, 0, 0)
        self.plugin_tree = QTreeWidget(plugin_panel)
        self.plugin_tree.setObjectName("pluginTree")
        self.plugin_tree.setHeaderLabels(["插件", ""])
        self.plugin_tree.setRootIsDecorated(False)
        plugin_layout.addWidget(self.plugin_tree)
        self.left_tabs.addTab(plugin_panel, "插件")

    # ---- 左栏: 插件管理 ----

    def reload_plugins(self) -> None:
        """插件面板重建: 全部插件记录 + 启用/禁用按钮."""
        self.plugin_tree.clear()
        for record in self.ctl.plugins.records():
            if record.manifest is None:
                continue
            manifest = record.manifest
            item = QTreeWidgetItem([manifest.name, ""])
            item.setToolTip(
                0, f"{manifest.id} v{manifest.version}\n"
                   f"权限: {'/'.join(manifest.permissions)}\n"
                   f"{manifest.description}")
            self.plugin_tree.addTopLevelItem(item)
            if record.status == "broken":
                item.setForeground(0, QBrush(Qt.GlobalColor.gray))
                item.setText(1, "损坏")
                continue
            enabled = record.status == "ok"
            if not enabled:
                item.setForeground(0, QBrush(Qt.GlobalColor.gray))
            button = _button("禁用" if enabled else "启用",
                             lambda _checked=False, pid=manifest.id,
                             en=not enabled: self._toggle_plugin(pid, en))
            self.plugin_tree.setItemWidget(item, 1, button)

    def _toggle_plugin(self, plugin_id: str, enable: bool) -> None:
        """启用/禁用插件(网络权限经确认), 随后刷新面板与相关页签."""

        def ask_network(manifest):
            return confirm_network_enable(self, manifest)

        confirm = None
        if enable:
            record = self.ctl.plugins.get(plugin_id)
            manifest = record.manifest if record is not None else None
            if manifest is not None and "network" in manifest.permissions:
                confirm = ask_network
        result = self.ctl.set_plugin_enabled(plugin_id, enable,
                                             confirm=confirm)
        self.statusBar().showMessage(result.message)
        self.file_page.set_plugin_catalog(self.ctl.plugins.catalog())
        self.reload_plugins()
        self._apply_plugin_tabs()

    def _apply_plugin_tabs(self) -> None:
        """服务型插件页签随启用状态显隐(禁用即隐藏, 状态保留)."""
        for plugin_id, tabs, widget in self._plugin_tabs:
            record = self.ctl.plugins.get(plugin_id)
            visible = record is not None and record.status == "ok"
            index = tabs.indexOf(widget)
            if index < 0:
                continue
            if not visible and tabs.currentWidget() is widget:
                tabs.setCurrentIndex(0)
            tabs.setTabVisible(index, visible)
            if visible:
                widget.refresh_state()

    def _show_campus_tab(self) -> None:
        """学堂页请求前往校园页签(仅在其可见时切换)."""
        index = self.tabs.indexOf(self.campus_tab)
        if index >= 0 and self.tabs.isTabVisible(index):
            self.tabs.setCurrentWidget(self.campus_tab)

    def _build_center(self) -> None:
        self.center_tabs = QTabWidget(self.splitter)
        self.file_page = FileBrowserPage(self.ctl, parent=self.center_tabs,
                                         clipboard=self._app_clipboard)
        self.file_page.add_requested.connect(self._on_add_requested)
        self.file_page.open_requested.connect(self._on_open_requested)
        self.file_page.status_message.connect(self.statusBar().showMessage)
        self.file_page.set_plugin_catalog(self.ctl.plugins.catalog())
        self.file_page.plugin_requested.connect(self._on_plugin_requested)
        self.center_tabs.addTab(self.file_page, "文件")
        self.links_page = LinksPage(self.ctl, self.center_tabs)
        self.center_tabs.addTab(self.links_page, "链接")
        self.learn_page = LearnPage(self.ctl, self.center_tabs)
        self.learn_page.status_message.connect(self.statusBar().showMessage)
        self.learn_page.campus_requested.connect(self._show_campus_tab)
        self.learn_page.resources_changed.connect(self.reload_courses)
        self.center_tabs.addTab(self.learn_page, "学堂")
        self.cloud_page = CloudPage(self.ctl, self.center_tabs)
        self.cloud_page.status_message.connect(self.statusBar().showMessage)
        self.cloud_page.resources_changed.connect(self.reload_courses)
        self.center_tabs.addTab(self.cloud_page, "云盘")
        self.center_tabs.currentChanged.connect(self._on_center_tab_changed)

    def _on_center_tab_changed(self, index: int) -> None:
        """切页兜底: 链接页重读 YAML, 学堂/云盘页重估状态(零联网)."""
        if self.center_tabs.widget(index) is self.links_page:
            self.links_page.reload()
        if self.center_tabs.widget(index) is self.learn_page:
            self.learn_page.refresh_state()
        if self.center_tabs.widget(index) is self.cloud_page:
            self.cloud_page.refresh_state()

    @property
    def workspace_root(self) -> str:
        """中栏工作目录(转发文件页)."""
        return self.file_page.workspace_root

    def set_workspace_root(self, path: str) -> bool:
        """调整中栏工作目录(转发文件页)."""
        return self.file_page.set_workspace_root(path)

    def _on_add_requested(self, path: str) -> None:
        """文件页请求登记: 目录递归导入为虚拟文件夹层级, 文件进目标文件夹.

        落点跟随左栏选中文件夹; 目录导入后展开新文件夹父链.
        """
        course_id, folder_id = self._current_target_folder()
        if course_id is None:
            self.statusBar().showMessage("请先在左栏选中一门课程")
            return
        if os.path.isdir(path):
            result = self.ctl.import_directory(course_id, path, folder_id)
        else:
            result = self.ctl.add_resource(course_id, path, folder_id=folder_id)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()
            if "folder_id" in result.detail:
                self._expand_to_new_folder(course_id,
                                           result.detail["folder_id"])

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
        self.campus_tab = CampusTab(self.ctl, self.tabs)
        self.tabs.addTab(self.campus_tab, "校园")

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
        """从索引重建课程树: 虚拟文件夹在前 + 索引条目在后, 展开状态记忆."""
        self.course_tree.clear()
        for course in self.storage.list_courses():
            node = QTreeWidgetItem(self.course_tree, [course["name"]])
            node.setData(0, ROLE_COURSE_ID, course["id"])
            node.setToolTip(0, f"课程: {course['name']}")
            node.setIcon(0, QIcon(artwork.kind_icon(
                "course", size=tree_icon_size())))
            self._fill_course_node(
                node,
                self.storage.list_folders(course["id"]),
                self.storage.list_resources(course["id"]))
            node.setExpanded(self._tree_expanded.get(
                ("course", course["id"]), False))

    def _fill_course_node(self, course_node,
                          folders: list, resources: list) -> None:
        """课程节点下组装层级: 文件夹递归在前(创建序), 索引条目在后(加入序)."""
        nodes = {None: course_node}
        by_parent: dict = {}
        for folder in folders:
            by_parent.setdefault(folder["parent_id"], []).append(folder)

        def build(parent_id, parent_item):
            for folder in by_parent.get(parent_id, []):
                item = QTreeWidgetItem(parent_item, [folder["name"]])
                item.setData(0, ROLE_FOLDER_ID, folder["id"])
                item.setToolTip(0, f"文件夹: {folder['name']}")
                item.setIcon(0, QIcon(artwork.kind_icon(
                    "dir", size=tree_icon_size())))
                item.setExpanded(self._tree_expanded.get(
                    ("folder", folder["id"]), False))
                nodes[folder["id"]] = item
                build(folder["id"], item)

        build(None, course_node)
        for res in resources:
            if res["kind"] == "dir":
                # 目录索引概念已废(2026-11-17): 目录经 import_directory
                # 导入为虚拟文件夹; 历史目录索引行不再显示
                continue
            exists, changed = Storage.check_resource(res)
            label = res["display_name"]
            if res["kind"] == "link":
                label = f"[{KIND_LABEL['link']}] " + label
            if not exists:
                label += " (失效)"
            elif changed:
                label += " (已变更)"
            # 归属文件夹行缺失(理论上仅外键兜底时)回落课程根
            item = QTreeWidgetItem(nodes.get(res["folder_id"], course_node),
                                   [label])
            item.setData(0, ROLE_RESOURCE_ID, res["id"])
            item.setToolTip(0, res["path_or_url"])
            item.setIcon(0, QIcon(artwork.kind_icon(
                res["kind"], failed=not exists,
                size=tree_icon_size())))
            if not exists:
                item.setForeground(0, QBrush(Qt.GlobalColor.gray))

    @staticmethod
    def _tree_state_key(item) -> tuple:
        """树项 -> 展开状态记忆键(课程/文件夹/资源三类)."""
        course_id = item.data(0, ROLE_COURSE_ID)
        if course_id is not None:
            return ("course", course_id)
        fid = item.data(0, ROLE_FOLDER_ID)
        if fid is not None:
            return ("folder", fid)
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is not None:
            return ("res", rid)
        return None

    def _remember_tree_state(self, item, expanded: bool) -> None:
        """记录用户展开/收起的节点, 重建树后恢复."""
        key = self._tree_state_key(item)
        if key is not None:
            self._tree_expanded[key] = expanded

    # ---- 左栏: 索引管理操作 (仅索引层, 不动磁盘文件) ----

    def _build_course_actions(self) -> None:
        """创建课程树操作 QAction(快捷键仅焦点在树内时生效)."""

        def make(text: str, slot, key: str = None) -> QAction:
            act = QAction(text, self.course_tree)
            if key:
                act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.course_tree.addAction(act)
            return act

        self.course_act_rename = make("重命名", self._rename_current, "F2")
        self.course_act_delete = make("删除", self._delete_current, "Delete")

    def _show_course_menu(self, pos) -> None:
        """右键课程树: 右键项切换为当前选中后按节点类型弹菜单."""
        item = self.course_tree.itemAt(pos)
        if item is not None:
            self.course_tree.setCurrentItem(item)
        menu = self._build_course_menu(item)
        if menu.actions():
            menu.exec(self.course_tree.viewport().mapToGlobal(pos))

    def _build_course_menu(self, item) -> QMenu:
        """构造右键菜单(与弹出分离, 便于测试); 操作全部整合于此.

        空白: 新建课程/刷新(无课程语境, 不给课程内操作); 课程: 另加
        新建目录/添加链接/删除课程; 文件夹: 新建子目录/添加链接/
        重命名/移动到/删除; 文件索引: 打开/定位/移动到/移除索引;
        链接: 打开/移动到/移除索引.
        """
        menu = QMenu(self.course_tree)
        if item is None:
            menu.addAction("新建课程", self._add_course)
            menu.addSeparator()
            menu.addAction("刷新", self.reload_courses)
            return menu
        if item.data(0, ROLE_COURSE_ID) is not None:
            menu.addAction("新建课程", self._add_course)
            menu.addAction("新建目录", self._folder_create)
            menu.addAction("添加链接", self._add_link)
            menu.addSeparator()
            menu.addAction("刷新", self.reload_courses)
            menu.addSeparator()
            menu.addAction("删除课程", self._delete_course)
            return menu
        if item.data(0, ROLE_FOLDER_ID) is not None:
            menu.addAction("新建子目录", self._folder_create)
            menu.addAction("添加链接", self._add_link)
            menu.addSeparator()
            menu.addAction(self.course_act_rename)
            menu.addAction("移动到...", self._move_current)
            menu.addAction(self.course_act_delete)
            return menu
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is not None:
            res = self.storage.get_resource(rid)
            if res is not None and res["kind"] != "link":
                menu.addAction("打开", lambda: self._on_resource_activated(item, 0))
                menu.addAction("在中栏定位", lambda: self._locate_resource(res))
            else:
                menu.addAction("打开", lambda: self._on_resource_activated(item, 0))
            menu.addSeparator()
            menu.addAction("移动到...", self._move_current)
            menu.addAction("移除索引", self._remove_selected)
        return menu

    def _course_id_of_item(self, item):
        """节点所属课程 id(向上找课程节点)."""
        node = item
        while node is not None and node.data(0, ROLE_COURSE_ID) is None:
            node = node.parent()
        return node.data(0, ROLE_COURSE_ID) if node is not None else None

    def _current_target_folder(self) -> tuple:
        """登记落点: (课程 id, 文件夹 id). 选中文件夹时落在该文件夹,
        其余(课程/资源/未选)落在课程根; 无课程语境返回 (None, None)."""
        item = self.course_tree.currentItem()
        if item is None:
            return None, None
        fid = item.data(0, ROLE_FOLDER_ID)
        course_id = self._course_id_of_item(item)
        return course_id, (fid if course_id is not None else None)

    def _folder_create(self) -> None:
        """新建虚拟文件夹: 落点 = 选中文件夹/课程根, 选中资源时提示."""
        item = self.course_tree.currentItem()
        if item is None:
            self.statusBar().showMessage("请先选中课程或文件夹")
            return
        course_id = self._course_id_of_item(item)
        if course_id is None:
            self.statusBar().showMessage("请先选中课程或文件夹")
            return
        parent_fid = item.data(0, ROLE_FOLDER_ID)
        name, ok = QInputDialog.getText(self, "新建文件夹", "名称:",
                                        text="新建文件夹")
        if not ok or not name.strip():
            return
        result = self.ctl.folder_create(course_id, parent_fid, name)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()
            self._expand_to_new_folder(course_id, result.detail["folder_id"])

    def _expand_to_new_folder(self, course_id: int, folder_id: int) -> None:
        """新建文件夹后展开其父链, 让新节点立即可见."""
        folder = self.storage.get_folder(folder_id)
        if folder is None:
            return
        chain = []
        fid = folder["parent_id"]
        while fid is not None:
            row = self.storage.get_folder(fid)
            chain.append(fid)
            fid = row["parent_id"] if row is not None else None
        for node in self._iter_course_items(course_id):
            if node.data(0, ROLE_FOLDER_ID) in chain:
                node.setExpanded(True)

    def _iter_course_items(self, course_id: int):
        """某课程节点下的全部树项(含嵌套, 广度优先)."""
        for i in range(self.course_tree.topLevelItemCount()):
            node = self.course_tree.topLevelItem(i)
            if node.data(0, ROLE_COURSE_ID) == course_id:
                queue = [node]
                while queue:
                    current = queue.pop(0)
                    yield current
                    queue.extend(current.child(j)
                                 for j in range(current.childCount()))

    def _rename_current(self) -> None:
        """F2: 重命名选中的文件夹(索引条目名跟随文件, 不提供改名)."""
        item = self.course_tree.currentItem()
        if item is None or item.data(0, ROLE_FOLDER_ID) is None:
            self.statusBar().showMessage("请先选中要重命名的文件夹")
            return
        fid = item.data(0, ROLE_FOLDER_ID)
        folder = self.storage.get_folder(fid)
        name, ok = QInputDialog.getText(self, "重命名文件夹", "新名称:",
                                        text=folder["name"])
        if not ok or not name.strip():
            return
        result = self.ctl.folder_rename(fid, name)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()

    def _delete_current(self) -> None:
        """Delete: 文件夹走删除流程(非空给一键清空选项), 索引条目走移除."""
        item = self.course_tree.currentItem()
        if item is None:
            return
        if item.data(0, ROLE_FOLDER_ID) is not None:
            self._folder_delete(item.data(0, ROLE_FOLDER_ID))
            return
        if item.data(0, ROLE_RESOURCE_ID) is not None:
            self._remove_selected()

    def _folder_delete(self, folder_id: int) -> None:
        """删除文件夹: 空则确认删; 非空提供\"全部移除并删除\"(仅动索引)."""
        folder = self.storage.get_folder(folder_id)
        if folder is None:
            return
        # 条目统计为全部子孙(子文件夹与其中索引总和), 非直接子项
        descendants = self.storage.folder_descendants(folder_id)
        sub_folders = len(descendants) - 1
        resources = self.storage.count_resources_in_folders(descendants)
        if not self.storage.count_folder_children(folder_id):
            answer = QMessageBox.question(
                self, "删除文件夹", f"删除空文件夹「{folder['name']}」?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            result = self.ctl.folder_delete(folder_id)
        else:
            box = QMessageBox(self)
            box.setWindowTitle("删除非空文件夹")
            box.setIcon(QMessageBox.Icon.Question)
            scope = f"{resources} 个索引条目" if resources else "无索引条目"
            if sub_folders:
                scope += f", {sub_folders} 个子文件夹"
            box.setText(f"文件夹「{folder['name']}」内共 {scope}.")
            box.setInformativeText(
                "可先移走其中条目再删除; 或连同子文件夹与其中索引一并移除"
                "(只动索引, 磁盘文件不受影响).")
            btn_remove = box.addButton("全部移除并删除",
                                       QMessageBox.ButtonRole.AcceptRole)
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(
                box.button(QMessageBox.StandardButton.Cancel))
            box.exec()
            if box.clickedButton() is not btn_remove:
                self.statusBar().showMessage("已取消删除文件夹")
                return
            result = self.ctl.folder_delete(folder_id, cascade=True)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()

    def _move_current(self) -> None:
        """移动到...: 文件夹或索引条目经 FolderPickerDialog 选目标."""
        item = self.course_tree.currentItem()
        if item is None:
            return
        course_id = self._course_id_of_item(item)
        if course_id is None:
            return
        course = self.storage.get_course(course_id)
        exclude = set()
        fid = item.data(0, ROLE_FOLDER_ID)
        if fid is not None:      # 移动文件夹: 自身与子孙不可为目标
            exclude = set(self.storage.folder_descendants(fid))
        dialog = dialogs.FolderPickerDialog(
            self.storage.list_folders(course_id), course["name"], self,
            exclude_ids=exclude)
        if not dialog.exec():
            return
        target = dialog.selected_folder_id()
        if fid is not None:
            result = self.ctl.folder_move(fid, target)
        else:
            rid = item.data(0, ROLE_RESOURCE_ID)
            result = self.ctl.resource_move(rid, target)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()

    def _locate_resource(self, res) -> None:
        """在中栏定位文件索引: 切到父目录并选中该文件."""
        path = res["path_or_url"]
        if not os.path.exists(path):
            self.statusBar().showMessage(f"已失效: {path}")
            return
        if not self.set_workspace_root(os.path.dirname(path)):
            return
        index = self.file_page.file_model.index(path)
        if index.isValid():
            self.file_page.file_view.setCurrentIndex(index)

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
        course_id, folder_id = self._current_target_folder()
        if course_id is None:
            self.statusBar().showMessage("请先在左栏选中一门课程")
            return
        url, ok = QInputDialog.getText(self, "添加链接", "URL(http/https):")
        if not ok or not url.strip():
            return
        result = self.ctl.add_resource(course_id, url.strip(),
                                       folder_id=folder_id)
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
            if item.data(0, ROLE_FOLDER_ID) is not None:
                self.statusBar().showMessage(
                    "选中的是文件夹, 请用「删除」移除文件夹")
            else:
                self.statusBar().showMessage(
                    "选中的是课程节点, 请用「删除课程」按钮")
            return
        res = self.storage.get_resource(rid)
        if res is None:
            return
        answer = QMessageBox.question(
            self, "移除索引", f"从索引移除(不动磁盘文件):\n{res['path_or_url']}?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.ctl.remove_resource(rid)
        self.statusBar().showMessage(result.message)
        self.reload_courses()

    def _on_resource_activated(self, item, _column) -> None:
        """双击索引条目: 链接用浏览器打开, 文件用系统默认程序打开."""
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is None:
            return                # 课程/文件夹节点: 双击仅切换展开
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
        if _open_local(path):
            self.statusBar().showMessage(f"已打开: {path}")
        else:
            warn(self, "无法打开文件",
                  f"系统没有可用于打开该文件的默认程序:\n{path}")

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
