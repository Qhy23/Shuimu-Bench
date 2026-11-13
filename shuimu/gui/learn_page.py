"""中栏"学堂"页签: 插件启用引导 / 校园登录引导 / 课程-作业-课件-公告.

数据全部经 controller.learn_* 动作; 构造与页签切换零联网(只读缓存与
会话文件). 刷新/下载为同步调用, 按课推进经 on_progress 更新进度文本
(网络慢时界面短暂冻结, 与校园页签同款取舍). 会话失效切回登录引导页,
缓存数据保留. "同步待办"按钮为下一版本占位.
"""

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shuimu.core.controller import Controller
from shuimu.core.learn import parse_learn_date
from shuimu.gui import theme
from shuimu.gui.dialogs import confirm_network_enable, warn

PAGE_DISABLED, PAGE_LOGIN, PAGE_MAIN = 0, 1, 2

_HOMEWORK_GROUPS = (("pending", "未交"), ("submitted", "已交未批"),
                    ("graded", "已批"))
_ROLE_KEY = Qt.ItemDataRole.UserRole


def _fmt_size(size) -> str:
    """字节数 -> 人类可读."""
    value = int(size or 0)
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} B"


def _stamp(value) -> str:
    """unix 时间戳 -> 'MM-DD HH:MM'; 空返回空串."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%m-%d %H:%M", time.localtime(seconds))


class HomeworkDetailDialog(QDialog):
    """作业描述只读对话框(富文本; 含下载附件入口)."""

    def __init__(self, title: str, html: str, parent=None,
                 has_attachment=None):
        super().__init__(parent)
        self.setWindowTitle(title or "作业描述")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        browser.setHtml(html or "<p>(无描述)</p>")
        layout.addWidget(browser)
        row = QHBoxLayout()
        self.download_button = None
        if has_attachment is not None:
            def trigger_attachment():
                has_attachment()
            self.download_button = QPushButton("下载附件")
            self.download_button.clicked.connect(trigger_attachment)
            row.addWidget(self.download_button)
        row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        layout.addLayout(row)


class LearnPage(QWidget):
    """中栏学堂页: 三态 + 课程/作业/课件/公告浏览与下载."""

    status_message = Signal(str)
    campus_requested = Signal()
    resources_changed = Signal()

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self._ctl = ctl
        self._seen_threshold = 0.0     # 早于此时间入库的条目不标"新"
        self._course_ids = []          # 行序 -> wlkcid
        self.homework_tree = None      # _build_homework_tab 赋值
        self.files_list = None         # _build_files_tab 赋值
        self.notice_list = None        # _build_notices_tab 赋值
        self.notice_browser = None     # _build_notices_tab 赋值
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self._build_disabled_page()
        self._build_login_page()
        self._build_main_page()
        self.refresh_state()

    # ---- 三页构建 ----

    def _build_disabled_page(self) -> None:
        """页 0: 未启用(说明 + 启用按钮)."""
        page = QWidget()
        column = QVBoxLayout(page)
        hint = QLabel("网络学堂插件可拉取本学期课程/作业/课件/公告, "
                      "课件与附件可下载并自动归档进本地课程.\n"
                      "该插件需要访问网络, 默认禁用; 需先登录校园账户.")
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.enable_button = QPushButton("启用学堂插件")
        self.enable_button.clicked.connect(self._enable_plugin)
        column.addWidget(self.enable_button)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_login_page(self) -> None:
        """页 1: 校园登录引导."""
        page = QWidget()
        column = QVBoxLayout(page)
        self.login_hint = QLabel("学堂数据需要校园登录态.\n"
                                 "请在右栏\"校园\"页签登录后回到本页.")
        self.login_hint.setWordWrap(True)
        column.addWidget(self.login_hint)
        goto_button = QPushButton("前往校园页签")
        goto_button.clicked.connect(self.campus_requested.emit)
        column.addWidget(goto_button)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_main_page(self) -> None:
        """页 2: 顶栏 + 左课程列表 + 右作业/课件/公告."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.semester_label = QLabel("")
        top.addWidget(self.semester_label)
        self.refreshed_label = QLabel("")
        self.refreshed_label.setStyleSheet(f"color: {theme.FG_DIM};")
        top.addWidget(self.refreshed_label)
        top.addStretch(1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {theme.FG_DIM};")
        self.progress_label.hide()
        top.addWidget(self.progress_label)
        self.todos_button = QPushButton("同步待办")
        self.todos_button.setEnabled(False)
        self.todos_button.setToolTip("未交作业同步进日程待办(下一版本提供)")
        top.addWidget(self.todos_button)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh)
        top.addWidget(self.refresh_button)
        column.addLayout(top)

        self.empty_label = QLabel("尚无学堂缓存, 点击\"刷新\"拉取本学期数据.")
        self.empty_label.setWordWrap(True)
        column.addWidget(self.empty_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.course_list = QListWidget()
        self.course_list.currentRowChanged.connect(self._on_course_changed)
        splitter.addWidget(self.course_list)

        self.detail_tabs = QTabWidget()
        self._build_homework_tab()
        self._build_files_tab()
        self._build_notices_tab()
        splitter.addWidget(self.detail_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        column.addWidget(splitter, 1)
        self.stack.addWidget(page)

    def _build_homework_tab(self) -> None:
        """子页 1: 作业三态树 + 底部按钮."""
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        self.homework_tree = QTreeWidget()
        self.homework_tree.setHeaderLabels(["作业", "信息"])
        self.homework_tree.setRootIsDecorated(True)
        self.homework_tree.itemDoubleClicked.connect(
            lambda item, _col: self._show_homework_detail(item))
        column.addWidget(self.homework_tree, 1)
        row = QHBoxLayout()
        detail_button = QPushButton("查看描述")
        detail_button.clicked.connect(
            lambda: self._show_homework_detail(
                self.homework_tree.currentItem()))
        row.addWidget(detail_button)
        attach_button = QPushButton("下载附件")
        attach_button.clicked.connect(self._download_homework_attachment)
        row.addWidget(attach_button)
        row.addStretch(1)
        column.addLayout(row)
        self.detail_tabs.addTab(container, "作业")

    def _build_files_tab(self) -> None:
        """子页 2: 课件列表 + 下载按钮."""
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        self.files_list = QListWidget()
        column.addWidget(self.files_list, 1)
        row = QHBoxLayout()
        download_button = QPushButton("下载选中课件")
        download_button.clicked.connect(self._download_selected_file)
        row.addWidget(download_button)
        row.addStretch(1)
        column.addLayout(row)
        self.detail_tabs.addTab(container, "课件")

    def _build_notices_tab(self) -> None:
        """子页 3: 公告列表 + 正文 + 附件按钮."""
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.notice_list = QListWidget()
        self.notice_list.currentRowChanged.connect(self._on_notice_changed)
        splitter.addWidget(self.notice_list)
        self.notice_browser = QTextBrowser()
        splitter.addWidget(self.notice_browser)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        column.addWidget(splitter, 1)
        row = QHBoxLayout()
        notice_button = QPushButton("下载附件")
        notice_button.clicked.connect(self._download_notice_attachment)
        row.addWidget(notice_button)
        row.addStretch(1)
        column.addLayout(row)
        self.detail_tabs.addTab(container, "公告")

    # ---- 状态机 ----

    def refresh_state(self) -> None:
        """按插件与会话状态切页(零联网); 进入主视图时重载数据."""
        record = self._ctl.plugins.get("learn")
        if record is None or record.status != "ok":
            self.stack.setCurrentIndex(PAGE_DISABLED)
            return
        result = self._ctl.campus_status()
        if result.detail.get("state") == "logged_out":
            self.stack.setCurrentIndex(PAGE_LOGIN)
            return
        self.reload()
        self.stack.setCurrentIndex(PAGE_MAIN)

    # ---- 数据装载 ----

    def reload(self) -> None:
        """只读缓存重建全部视图."""
        threshold = self._ctl.storage.learn_meta_get("refreshed_at")
        try:
            self._seen_threshold = float(threshold)
        except (TypeError, ValueError):
            self._seen_threshold = 0.0
        semester = self._ctl.storage.learn_meta_get("semester_id") or ""
        self.semester_label.setText(f"学期 {semester}".strip())
        self.refreshed_label.setText(
            f"上次刷新 {_stamp(self._seen_threshold)}".strip())
        self._fill_courses()

    def _fill_courses(self) -> None:
        """课程列表(关联标记); 保持当前选中."""
        rows = self._ctl.storage.learn_list_courses()
        names = {c["id"]: c["name"]
                 for c in self._ctl.storage.list_courses()}
        current = self._current_wlkcid()
        self.course_list.blockSignals(True)
        self.course_list.clear()
        self._course_ids = []
        restore = 0
        for index, row in enumerate(rows):
            self._course_ids.append(row["wlkcid"])
            if row["wlkcid"] == current:
                restore = index
            if row["course_id"] is not None:
                text = f"● {row['kcm']}"
                tooltip = f"已关联本地课程: {names.get(row['course_id'])}"
            else:
                text = row["kcm"]
                tooltip = "未关联本地课程(下载仍落盘, 不自动登记)"
            item = QListWidgetItem(text)
            item.setToolTip(tooltip)
            self.course_list.addItem(item)
        self.course_list.blockSignals(False)
        has_rows = bool(rows)
        self.empty_label.setVisible(not has_rows)
        self.course_list.setVisible(has_rows)
        self.detail_tabs.setVisible(has_rows)
        if has_rows:
            self.course_list.setCurrentRow(restore)
            self._load_course(rows[restore]["wlkcid"] if restore < len(rows)
                              else rows[0]["wlkcid"])

    def _current_wlkcid(self):
        """当前选中课程的 wlkcid."""
        row = self.course_list.currentRow()
        if 0 <= row < len(self._course_ids):
            return self._course_ids[row]
        return None

    def _on_course_changed(self, row: int) -> None:
        """课程切换 -> 装载三门数据."""
        if 0 <= row < len(self._course_ids):
            self._load_course(self._course_ids[row])

    def _load_course(self, wlkcid: str) -> None:
        """装载一门的作业/课件/公告."""
        self._fill_homework(wlkcid)
        self._fill_files(wlkcid)
        self._fill_notices(wlkcid)

    def _mark_new(self, row, item) -> None:
        """first_seen_at 晚于阈值 -> 条目标\"新\"."""
        try:
            if float(row["first_seen_at"]) > self._seen_threshold:
                item.setText(f"[新] {item.text()}")
        except (TypeError, ValueError):
            pass

    def _fill_homework(self, wlkcid: str) -> None:
        """作业树三分组."""
        rows = self._ctl.storage.learn_list_homework(wlkcid=wlkcid)
        self.homework_tree.clear()
        for state, label in _HOMEWORK_GROUPS:
            count = len([r for r in rows if r["state"] == state])
            group = QTreeWidgetItem([f"{label} ({count})", ""])
            self.homework_tree.addTopLevelItem(group)
            for row in rows:
                if row["state"] != state:
                    continue
                if state == "pending":
                    note = f"截止 {parse_learn_date(row['jzsj']) or '-'}"
                    if row["sfbj"] == "1":
                        note += "(可补交)"
                elif state == "submitted":
                    note = f"提交 {parse_learn_date(row['scsj']) or '-'}"
                else:
                    note = f"成绩 {row['cj'] or '-'}"
                    if row["pynr"]:
                        note += f" | {row['pynr'][:20]}"
                item = QTreeWidgetItem([row["bt"] or row["zyid"], note])
                item.setData(0, _ROLE_KEY, row["zyid"])
                self._mark_new(row, item)
                group.addChild(item)
        self.homework_tree.expandAll()

    def _fill_files(self, wlkcid: str) -> None:
        """课件列表(单行, 元信息在 tooltip; 已下载标记)."""
        self.files_list.clear()
        for row in self._ctl.storage.learn_list_files(wlkcid):
            downloaded = ""
            if row["wjid"]:
                record = self._ctl.storage.learn_find_download(
                    "file", row["wjid"])
                if record is not None:
                    downloaded = "  [已下载]"
            meta = (f"{row['category'] or '未分类'} | "
                    f"{_fmt_size(row['wjdx'])} | 上传 {row['scsj'] or '-'}")
            item = QListWidgetItem(f"{row['bt']}{downloaded}   ·   {meta}")
            item.setToolTip(f"{row['bt']}\n{meta}\n文件 id: {row['wjid']}")
            item.setData(_ROLE_KEY, row["wjid"])
            self._mark_new(row, item)
            self.files_list.addItem(item)

    def _fill_notices(self, wlkcid: str) -> None:
        """公告列表(单行)与正文."""
        self.notice_list.clear()
        self.notice_browser.clear()
        for row in self._ctl.storage.learn_list_notices(wlkcid):
            read = "" if row["sfyd"] == "1" else "  [未读]"
            item = QListWidgetItem(f"{row['bt']}{read}   ·   "
                                   f"{row['fbsj'] or '-'}")
            item.setToolTip(f"{row['bt']}\n发布 {row['fbsj'] or '-'}"
                            f"\n附件: {row['fjmc'] or '无'}")
            item.setData(_ROLE_KEY, row["ggid"])
            self._mark_new(row, item)
            self.notice_list.addItem(item)

    def _on_notice_changed(self, row: int) -> None:
        """公告选中 -> 显示正文."""
        item = self.notice_list.item(row)
        if item is None:
            return
        wlkcid = self._current_wlkcid()
        if wlkcid is None:
            return
        ggid = item.data(_ROLE_KEY)
        for notice in self._ctl.storage.learn_list_notices(wlkcid):
            if notice["ggid"] == ggid:
                self.notice_browser.setHtml(notice["ggnr"] or
                                            "<p>(无正文)</p>")
                return

    # ---- 动作 ----

    def _enable_plugin(self) -> None:
        """启用学堂插件(经网络权限确认框)."""
        result = self._ctl.set_plugin_enabled(
            "learn", True,
            confirm=lambda manifest: confirm_network_enable(self, manifest))
        if not result.ok:
            warn(self, "未能启用学堂插件", result.message)
        self.refresh_state()

    def _refresh(self) -> None:
        """同步全量刷新; 按课推进进度文本; 失效切登录引导."""
        self.refresh_button.setEnabled(False)
        self.progress_label.show()

        def on_progress(_stage, index, total, kcm):
            text = (f"正在刷新 {index}/{total}: {kcm}" if kcm
                    else f"完成, 共 {total} 门课")
            self.progress_label.setText(text)
            QApplication.processEvents()

        result = self._ctl.learn_refresh(on_progress=on_progress)
        self.refresh_button.setEnabled(True)
        self.progress_label.hide()
        self.status_message.emit(result.message)
        if result.ok:
            self.reload()
            return
        if result.detail.get("state") == "expired":
            self.login_hint.setText("校园会话已失效, 请在右栏\"校园\"页签"
                                    "重新登录后回到本页.\n(已有缓存数据保留)")
            self.stack.setCurrentIndex(PAGE_LOGIN)
            warn(self, "学堂刷新", "校园会话已失效, 请重新登录校园账户")

    # -- 作业 --

    def _selected_homework(self):
        """当前选中的作业叶子(组节点返回 None)."""
        item = self.homework_tree.currentItem()
        if item is None or item.parent() is None:
            return None
        return item.data(0, _ROLE_KEY)

    def _show_homework_detail(self, item) -> None:
        """弹作业描述(缓存优先, 缺失联网拉取)."""
        if item is None or item.parent() is None:
            return
        zyid = item.data(0, _ROLE_KEY)
        wlkcid = self._current_wlkcid()
        if zyid is None or wlkcid is None:
            return
        result = self._ctl.learn_homework_detail(wlkcid, zyid)
        if not result.ok:
            self.status_message.emit(result.message)
            return
        dialog = HomeworkDetailDialog(
            item.text(0).replace("[新] ", ""), result.detail.get("html"),
            self, has_attachment=lambda: self._download_attachment(
                "homework", zyid))
        dialog.exec()

    def _download_homework_attachment(self) -> None:
        """下载选中作业的附件."""
        zyid = self._selected_homework()
        if zyid is None:
            self.status_message.emit("请先选中一份作业")
            return
        self._download_attachment("homework", zyid)

    # -- 课件与公告 --

    def _download_selected_file(self) -> None:
        """下载选中的课件."""
        item = self.files_list.currentItem()
        if item is None:
            self.status_message.emit("请先选中一个课件")
            return
        wjid = item.data(_ROLE_KEY)
        if not wjid:
            self.status_message.emit("该课件没有可下载文件")
            return
        self._download_attachment("file", wjid)

    def _download_notice_attachment(self) -> None:
        """下载选中公告的附件."""
        item = self.notice_list.currentItem()
        if item is None:
            self.status_message.emit("请先选中一条公告")
            return
        self._download_attachment("notice", item.data(_ROLE_KEY))

    def _download_attachment(self, kind: str, remote_id: str) -> None:
        """统一下载动作: 提示结果, 登记成功后刷新左栏与列表."""
        result = self._ctl.learn_download(kind, remote_id)
        self.status_message.emit(result.message)
        if result.ok:
            if result.detail.get("resource_id") is not None:
                self.resources_changed.emit()
            wlkcid = self._current_wlkcid()
            if wlkcid is not None:
                self._fill_files(wlkcid)
