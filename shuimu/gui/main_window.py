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
    QAbstractItemView,
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
ROLE_DIR_PATH = Qt.UserRole + 2


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


def _display_forest(resources: list) -> list:
    """课程资源 -> 显示树节点列表(纯数据, 无 Qt, 便于单测).

    文件/目录资源按绝对路径组件嵌套, 连续单子虚拟目录折叠为最深节点;
    链接无层级, 随加入顺序平铺。节点形如 {"kind": "dir"|"resource",
    "path": 完整路径|None, "resource": 资源行|None, "children": [...]};
    输出保持资源加入顺序, 文件/目录的顶层组在其首个资源出现处整组发射.
    """
    root = {"resource": None, "children": {}}
    for res in resources:
        if res["kind"] == "link":
            continue
        node = root
        for part in res["path_or_url"].split("/")[1:]:
            node = node["children"].setdefault(
                part, {"resource": None, "children": {}})
        node["resource"] = res
    out: list = []
    emitted: set = set()
    for res in resources:
        if res["kind"] == "link":
            out.append({"kind": "resource", "path": None,
                        "resource": res, "children": []})
            continue
        parts = res["path_or_url"].split("/")[1:]
        if not parts:  # 根目录本身被登记的病态情况: 不参与分组
            if "root" not in emitted:
                emitted.add("root")
                out.append({"kind": "resource", "path": None,
                            "resource": res, "children": []})
            continue
        if parts[0] in emitted:
            continue
        emitted.add(parts[0])
        out.append(_emit_group(root["children"][parts[0]], ["", parts[0]]))
    return out


def _emit_group(node: dict, path: list) -> dict:
    """从 trie 节点发射显示子树: 先折叠单链, 虚拟节点记录完整路径."""
    while node["resource"] is None and len(node["children"]) == 1:
        name, node = next(iter(node["children"].items()))
        path = path + [name]
    base = (path if node["resource"] is None
            else node["resource"]["path_or_url"].split("/"))
    children = [_emit_group(child, base + [name])
                for name, child in node["children"].items()]
    if node["resource"] is None:
        return {"kind": "dir", "path": "/".join(path),
                "resource": None, "children": children}
    return {"kind": "resource", "path": None,
            "resource": node["resource"], "children": children}


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
        # 文件管理器化: 多选 + 右键菜单 + 快捷键(与中栏文件页同款语义)
        self.course_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.course_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.course_tree.customContextMenuRequested.connect(
            self._show_course_menu)
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
        """从索引重建课程树: 资源按目录层级嵌套, 失效标灰, 展开状态记忆."""
        self.course_tree.clear()
        for course in self.storage.list_courses():
            node = QTreeWidgetItem(self.course_tree, [course["name"]])
            node.setData(0, ROLE_COURSE_ID, course["id"])
            node.setToolTip(0, f"课程: {course['name']}")
            node.setIcon(0, QIcon(artwork.kind_icon(
                "course", size=tree_icon_size())))
            for dnode in _display_forest(self.storage.list_resources(course["id"])):
                self._append_tree_node(node, dnode, course["id"])
            node.setExpanded(self._tree_expanded.get(
                ("course", course["id"]), False))

    def _append_tree_node(self, parent_item, dnode: dict,
                          course_id: int) -> None:
        """把显示树节点挂为 Qt 项: 虚拟目录或资源两态."""
        if dnode["kind"] == "dir":
            item = QTreeWidgetItem(parent_item,
                                   [os.path.basename(dnode["path"])])
            item.setData(0, ROLE_DIR_PATH, dnode["path"])
            item.setToolTip(0, dnode["path"])
            item.setIcon(0, QIcon(artwork.kind_icon(
                "dir", size=tree_icon_size())))
            key = ("dir", course_id, dnode["path"])
        else:
            res = dnode["resource"]
            exists, changed = Storage.check_resource(res)
            label = res["display_name"]
            if res["kind"] == "link":
                label = f"[{KIND_LABEL['link']}] " + label
            if not exists:
                label += " (失效)"
            elif changed:
                label += " (已变更)"
            item = QTreeWidgetItem(parent_item, [label])
            item.setData(0, ROLE_RESOURCE_ID, res["id"])
            item.setToolTip(0, res["path_or_url"])
            item.setIcon(0, QIcon(artwork.kind_icon(
                res["kind"], failed=not exists,
                size=tree_icon_size())))
            if not exists:
                item.setForeground(0, QBrush(Qt.GlobalColor.gray))
            key = ("res", res["id"])
        item.setExpanded(self._tree_expanded.get(key, False))
        for child in dnode["children"]:
            self._append_tree_node(item, child, course_id)

    @staticmethod
    def _tree_state_key(item) -> tuple:
        """树项 -> 展开状态记忆键(课程/资源/虚拟目录三类)."""
        course_id = item.data(0, ROLE_COURSE_ID)
        if course_id is not None:
            return ("course", course_id)
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is not None:
            return ("res", rid)
        path = item.data(0, ROLE_DIR_PATH)
        if path is None:
            return None
        parent = item.parent()
        while parent is not None and parent.data(0, ROLE_COURSE_ID) is None:
            parent = parent.parent()
        course_id = parent.data(0, ROLE_COURSE_ID) if parent else None
        return ("dir", course_id, path)

    def _remember_tree_state(self, item, expanded: bool) -> None:
        """记录用户展开/收起的节点, 重建树后恢复."""
        key = self._tree_state_key(item)
        if key is not None:
            self._tree_expanded[key] = expanded

    # ---- 左栏: 课程树文件操作 (与中栏文件页同款语义) ----

    def _build_course_actions(self) -> None:
        """创建课程树操作 QAction(快捷键仅焦点在树内时生效, 对齐文件页)."""

        def make(text: str, slot, key: str = None) -> QAction:
            act = QAction(text, self.course_tree)
            if key:
                act.setShortcut(QKeySequence(key))
            act.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.course_tree.addAction(act)
            return act

        self.course_act_copy = make("复制", self._course_copy, "Ctrl+C")
        self.course_act_cut = make("剪切", self._course_cut, "Ctrl+X")
        self.course_act_paste = make("粘贴", self._course_paste, "Ctrl+V")
        self.course_act_delete = make("删除", self._course_delete, "Delete")
        self.course_act_rename = make("重命名", self._course_rename, "F2")
        self.course_act_mkdir = make("新建文件夹", self._course_mkdir)
        self.course_act_undo = make("撤销", self._course_undo, "Ctrl+Z")

    def _show_course_menu(self, pos) -> None:
        """右键课程树: 右键项不在选中集内时先切换选中(对齐文件页语义)."""
        item = self.course_tree.itemAt(pos)
        if item is not None and item not in self.course_tree.selectedItems():
            self.course_tree.setCurrentItem(item)
        self._build_course_menu(item).exec(
            self.course_tree.viewport().mapToGlobal(pos))

    def _build_course_menu(self, item) -> QMenu:
        """构造右键菜单(与弹出分离, 便于测试).

        链接节点只开放/移除; 文件/目录节点给全套文件操作; 目录节点
        追加粘贴与新建文件夹; 课程节点与空白处只保留撤销.
        """
        menu = QMenu(self.course_tree)
        if item is None or item.data(0, ROLE_COURSE_ID) is not None:
            menu.addAction(self.course_act_undo)
            return menu
        path = self._tree_node_path(item)
        is_link = item.data(0, ROLE_RESOURCE_ID) is not None and path is None
        is_dir = self._tree_node_is_dir(item)
        if is_link:
            menu.addAction("打开", lambda: self._on_resource_activated(item, 0))
            menu.addAction("移除资源", self._remove_selected)
        else:
            menu.addAction("在中栏定位" if is_dir else "打开",
                           lambda: self._on_resource_activated(item, 0))
            if item.data(0, ROLE_RESOURCE_ID) is not None:
                menu.addAction("移除资源", self._remove_selected)
            menu.addSeparator()
            menu.addAction(self.course_act_copy)
            menu.addAction(self.course_act_cut)
            if is_dir:
                menu.addAction(self.course_act_paste)
            menu.addSeparator()
            menu.addAction(self.course_act_rename)
            menu.addAction(self.course_act_delete)
            if is_dir:
                menu.addAction(self.course_act_mkdir)
        menu.addSeparator()
        menu.addAction(self.course_act_undo)
        self.course_act_paste.setEnabled(bool(self._app_clipboard["paths"]))
        return menu

    def _tree_node_path(self, item):
        """节点 -> 文件系统路径(链接与课程节点返回 None)."""
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is not None:
            res = self.storage.get_resource(rid)
            if res is None or res["kind"] == "link":
                return None
            return res["path_or_url"]
        return item.data(0, ROLE_DIR_PATH)

    def _tree_node_is_dir(self, item) -> bool:
        """节点是否目录(登记目录或虚拟目录)."""
        rid = item.data(0, ROLE_RESOURCE_ID)
        if rid is not None:
            res = self.storage.get_resource(rid)
            return res is not None and res["kind"] == "dir"
        return item.data(0, ROLE_DIR_PATH) is not None

    def _tree_fs_selection(self) -> tuple:
        """选中节点分拣 -> (可操作路径列表, 被忽略的链接数). 课程节点跳过."""
        paths, links = [], 0
        for node in self.course_tree.selectedItems():
            path = self._tree_node_path(node)
            if path is not None:
                paths.append(path)
            elif node.data(0, ROLE_RESOURCE_ID) is not None:
                links += 1
        return paths, links

    def _selected_dir_node(self):
        """恰好选中一个目录节点时返回其路径, 否则 None."""
        nodes = [n for n in self.course_tree.selectedItems()
                 if n.data(0, ROLE_COURSE_ID) is None]
        if len(nodes) != 1:
            return None
        return self._tree_node_path(nodes[0]) \
            if self._tree_node_is_dir(nodes[0]) else None

    def _course_id_of_item(self, item):
        """节点所属课程 id(向上找课程节点)."""
        node = item
        while node is not None and node.data(0, ROLE_COURSE_ID) is None:
            node = node.parent()
        return node.data(0, ROLE_COURSE_ID) if node is not None else None

    def _course_copy(self) -> None:
        paths, links = self._tree_fs_selection()
        if not paths:
            self.statusBar().showMessage("请先选中文件或目录")
            return
        self._app_clipboard["mode"] = "copy"
        self._app_clipboard["paths"] = paths
        self.statusBar().showMessage(
            f"已复制 {len(paths)} 项" + (f"(忽略 {links} 个链接)" if links else ""))

    def _course_cut(self) -> None:
        paths, links = self._tree_fs_selection()
        if not paths:
            self.statusBar().showMessage("请先选中文件或目录")
            return
        self._app_clipboard["mode"] = "cut"
        self._app_clipboard["paths"] = paths
        self.statusBar().showMessage(
            f"已剪切 {len(paths)} 项(粘贴后生效)"
            + (f"(忽略 {links} 个链接)" if links else ""))

    def _course_paste(self) -> None:
        """粘贴到选中的目录节点, 贴入项自动登记进该课程.

        冲突决策与执行在 dialogs.perform_paste(与中栏共用内核),
        此处注入登记回调.
        """
        target = self._selected_dir_node()
        if target is None:
            self.statusBar().showMessage("请先选中一个目录节点再粘贴")
            return
        if not self._app_clipboard["paths"]:
            self.statusBar().showMessage("剪贴板为空")
            return
        course_id = self._course_id_of_item(self.course_tree.currentItem())
        if course_id is None:
            self.statusBar().showMessage("无法确定目标课程")
            return

        def register(dst: str) -> bool:
            return self.ctl.add_resource(course_id, dst).ok

        result = dialogs.perform_paste(
            self.ctl, self._app_clipboard, target,
            lambda name, remaining: dialogs.ask_conflict(name, remaining, self),
            register=register)
        self.statusBar().showMessage(result.message)
        self.reload_courses()

    def _course_delete(self) -> None:
        """删除选中文件/目录: 移入回收站, 索引行同步移除."""
        paths, _links = self._tree_fs_selection()
        if not paths:
            self.statusBar().showMessage("请先选中文件或目录")
            return
        names = [os.path.basename(p) for p in paths]
        preview = "\n".join(names[:5]) + ("\n..." if len(names) > 5 else "")
        answer = QMessageBox.question(
            self, "删除",
            f"把选中的 {len(paths)} 个项目移入回收站?\n\n{preview}\n\n"
            "可从回收站找回; 对应课程索引将一并移除.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.ctl.file_delete(paths)
        self.statusBar().showMessage(result.message)
        self.reload_courses()

    def _course_rename(self) -> None:
        """重命名选中节点(虚拟目录同样可改, 索引路径同步含子孙)."""
        item = self.course_tree.currentItem()
        if item is None or item.data(0, ROLE_COURSE_ID) is not None:
            self.statusBar().showMessage("请先选中要重命名的项目")
            return
        path = self._tree_node_path(item)
        if path is None:
            self.statusBar().showMessage("链接不支持重命名, 请编辑对应课程资源")
            return
        name, ok = QInputDialog.getText(self, "重命名", "新名称:",
                                        text=os.path.basename(path))
        if not ok:
            return
        if not name.strip() or name.strip() == os.path.basename(path):
            self.statusBar().showMessage("名称未变化")
            return
        result = self.ctl.file_rename(path, name)
        self.statusBar().showMessage(result.message)
        if result.ok:
            self.reload_courses()

    def _course_mkdir(self) -> None:
        """在选中目录节点下新建文件夹并自动登记进该课程."""
        target = self._selected_dir_node()
        if target is None:
            self.statusBar().showMessage("请先选中一个目录节点")
            return
        course_id = self._course_id_of_item(self.course_tree.currentItem())
        if course_id is None:
            self.statusBar().showMessage("无法确定目标课程")
            return
        name, ok = QInputDialog.getText(self, "新建文件夹", "名称:",
                                        text="新建文件夹")
        if not ok:
            return
        result = self.ctl.file_mkdir(target, name)
        if result.ok:
            self.ctl.add_resource(course_id, result.detail["path"])
        self.statusBar().showMessage(result.message)
        self.reload_courses()

    def _course_undo(self) -> None:
        """撤销最近一次文件操作(文件与索引一并回滚)."""
        result = self.ctl.undo()
        self.statusBar().showMessage(result.message)
        self.reload_courses()

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
            if item.data(0, ROLE_DIR_PATH) is not None:
                self.statusBar().showMessage(
                    "选中的是目录层级节点(未登记), 请选中具体资源移除")
            else:
                self.statusBar().showMessage(
                    "选中的是课程节点, 请用「删除课程」按钮")
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
            # 虚拟目录节点: 与目录资源一致, 双击在中栏定位
            path = item.data(0, ROLE_DIR_PATH)
            if path is not None:
                if not os.path.exists(path):
                    self.statusBar().showMessage(f"已失效: {path}")
                else:
                    self.set_workspace_root(path)
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
