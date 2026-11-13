"""中栏"云盘"页签: 插件启用引导 / 分享链接浏览与挂载.

清华云盘分享链接免登录(公开分享): 粘贴链接 -> 浏览目录树并按课程名
自动匹配 -> 一键把匹配目录登记为课程链接资源. 数据全部经
controller.cloud_* 动作; 浏览只读不留痕, 挂载登记并留痕.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shuimu.core.controller import Controller
from shuimu.gui import theme
from shuimu.gui.dialogs import confirm_network_enable, warn

PAGE_DISABLED, PAGE_MAIN = 0, 1

_ROLE_URL = Qt.ItemDataRole.UserRole


class CloudPage(QWidget):
    """中栏云盘页: 两态(未启用/主视图) + 浏览/挂载."""

    status_message = Signal(str)
    resources_changed = Signal()

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self._ctl = ctl
        self._browse_url = ""          # 最近浏览成功的规范链接
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self._build_disabled_page()
        self._build_main_page()
        self.refresh_state()

    # ---- 两页构建 ----

    def _build_disabled_page(self) -> None:
        """页 0: 未启用(说明 + 启用按钮)."""
        page = QWidget()
        column = QVBoxLayout(page)
        hint = QLabel("清华云盘插件可把云盘分享资料库中与子课程同名的"
                      "目录挂载为课程链接资源.\n"
                      "分享链接免登录(公开分享); 该插件需要访问网络,"
                      " 默认禁用.")
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.enable_button = QPushButton("启用云盘插件")
        self.enable_button.clicked.connect(self._enable_plugin)
        column.addWidget(self.enable_button)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_main_page(self) -> None:
        """页 1: 链接输入 + 目录/课程匹配表."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "粘贴云盘分享链接, 如 https://cloud.tsinghua.edu.cn/d/<分享id>/")
        top.addWidget(self.url_edit, 1)
        self.browse_button = QPushButton("浏览")
        self.browse_button.clicked.connect(self._browse)
        top.addWidget(self.browse_button)
        self.mount_button = QPushButton("挂载到课程")
        self.mount_button.setToolTip(
            "把匹配到课程的目录登记为课程链接资源(重复挂载自动跳过)")
        self.mount_button.setEnabled(False)
        self.mount_button.clicked.connect(self._mount)
        top.addWidget(self.mount_button)
        column.addLayout(top)

        self.share_label = QLabel("")
        self.share_label.setStyleSheet(f"color: {theme.FG_DIM};")
        column.addWidget(self.share_label)
        self.hint_label = QLabel("浏览后, 与子课程同名的目录会自动匹配;"
                                 " 点击\"挂载到课程\"登记为链接资源.")
        self.hint_label.setWordWrap(True)
        column.addWidget(self.hint_label)

        self.dir_tree = QTreeWidget()
        self.dir_tree.setHeaderLabels(["目录", "匹配课程", "状态"])
        self.dir_tree.setColumnWidth(0, 260)
        self.dir_tree.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        column.addWidget(self.dir_tree, 1)
        self.stack.addWidget(page)

    # ---- 状态机 ----

    def refresh_state(self) -> None:
        """按插件状态切页(零联网)."""
        record = self._ctl.plugins.get("cloud")
        if record is None or record.status != "ok":
            self.stack.setCurrentIndex(PAGE_DISABLED)
            return
        self.stack.setCurrentIndex(PAGE_MAIN)

    # ---- 动作 ----

    def _enable_plugin(self) -> None:
        """启用云盘插件(经网络权限确认框)."""
        result = self._ctl.set_plugin_enabled(
            "cloud", True,
            confirm=lambda manifest: confirm_network_enable(self, manifest))
        if not result.ok:
            warn(self, "未能启用云盘插件", result.message)
        self.refresh_state()

    def _browse(self) -> None:
        """浏览分享库并填充匹配表."""
        url = self.url_edit.text().strip()
        if not url:
            self.hint_label.setText("请先粘贴分享链接.")
            return
        self._set_busy(True, "正在浏览分享库...")
        result = self._ctl.cloud_browse(url)
        self._set_busy(False)
        self.status_message.emit(result.message)
        if not result.ok:
            self.hint_label.setText(result.message)
            self.share_label.setText("")
            self._browse_url = ""
            self.mount_button.setEnabled(False)
            return
        self._browse_url = url
        self.hint_label.setText(result.message)
        self._fill_plans(result.detail)

    def _mount(self) -> None:
        """把匹配目录登记为课程链接资源."""
        if not self._browse_url:
            return
        self._set_busy(True, "正在挂载...")
        result = self._ctl.cloud_mount(self._browse_url)
        self._set_busy(False)
        self.status_message.emit(result.message)
        if not result.ok:
            self.hint_label.setText(result.message)
            return
        self.hint_label.setText(result.message)
        mounted = {item["dir"] for item in result.detail.get("mounted") or []}
        skipped = {name for _c, name in result.detail.get("skipped") or []}
        for row in range(self.dir_tree.topLevelItemCount()):
            item = self.dir_tree.topLevelItem(row)
            name = item.text(0)
            if name in mounted or name in skipped:
                item.setText(2, "已挂载")
        self.resources_changed.emit()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        """同步网络调用期间禁用按钮并提示(与学堂页同款取舍)."""
        self.browse_button.setEnabled(not busy)
        self.mount_button.setEnabled(
            not busy and bool(self._browse_url))
        if busy:
            self.share_label.setText(text)
            QApplication.processEvents()

    def _fill_plans(self, detail: dict) -> None:
        """浏览结果填充: 目录 -> 匹配课程/未匹配."""
        title = detail.get("share") or "云盘资料库"
        self.share_label.setText(f"«{title}»")
        mounts = {m["name"]: m["course_name"]
                  for m in detail.get("mounts") or []}
        unmatched = list(detail.get("unmatched") or [])
        rows = ([(name, mounts[name], "待挂载") for name in mounts]
                + [(name, "", "未匹配") for name in unmatched])
        rows.sort(key=lambda row: row[0])
        self.dir_tree.clear()
        for name, course, state in rows:
            item = QTreeWidgetItem([name, course, state])
            if not course:
                item.setForeground(1, Qt.GlobalColor.gray)
            self.dir_tree.addTopLevelItem(item)
        self.mount_button.setEnabled(bool(mounts))
