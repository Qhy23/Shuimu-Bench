"""日程页签: 周历(课表+事件)与待办列表.

上段周历 7 天列(横向滚动, 今天列高亮, 可翻周); 下段待办树(勾选完成/逾期
红色/完成划线置底). 渲染完全由 controller.schedule_snapshot() 全量驱动;
课表与事件在外部编辑, QFileSystemWatcher 自动重载; 待办可 GUI 新建与勾选,
经 Controller 写回 YAML 并留痕.
"""

import os
from datetime import date, timedelta

from PySide6.QtCore import Qt, QFileSystemWatcher
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .controller import Controller
from .schedule import (
    clamp_to_semester,
    entries_for_week,
    semester_week,
    sorted_todos,
    todo_status,
    week_start,
)
from .week_grid import WeekGridWidget

ROLE_TID = Qt.UserRole + 2


def _warn(parent, title: str, text: str) -> None:
    """弹出警告对话框(独立成函数便于测试替换)."""
    QMessageBox.warning(parent, title, text)


class TodoDialog(QDialog):
    """新建待办对话框: 标题必填, 截止可选, 课程可选."""

    def __init__(self, course_names: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建待办")
        form = QFormLayout(self)
        self.title_edit = QLineEdit(self)
        self.title_edit.setPlaceholderText("必填, 如: 微积分作业 6")
        form.addRow("标题", self.title_edit)
        self.due_check = QCheckBox("设置截止", self)
        self.due_edit = QDateEdit(self)
        self.due_edit.setCalendarPopup(True)
        self.due_edit.setDisplayFormat("yyyy-MM-dd")
        self.due_edit.setDate(self.due_edit.date().currentDate())
        self.due_edit.setEnabled(False)
        self.due_check.toggled.connect(self.due_edit.setEnabled)
        form.addRow(self.due_check, self.due_edit)
        self.course_box = QComboBox(self)
        self.course_box.addItem("(不关联)")
        self.course_box.addItems(course_names)
        form.addRow("课程", self.course_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept_if_valid(self) -> None:
        """标题为空时警告且不关闭."""
        if not self.title_edit.text().strip():
            _warn(self, "无法创建", "标题不能为空")
            return
        self.accept()

    def values(self) -> tuple:
        """返回 (标题, 截止 date 或 None, 课程名或空串)."""
        due = None
        if self.due_check.isChecked():
            due = self.due_edit.date().toPython()
        course = ""
        if self.course_box.currentIndex() > 0:
            course = self.course_box.currentText()
        return self.title_edit.text().strip(), due, course


class ScheduleTab(QWidget):
    """日程页签: 上段周历, 下段待办."""

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._self_write = False
        self._monday = week_start(date.today())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.warn_bar = QLabel(self)
        self.warn_bar.setObjectName("warnbar")
        self.warn_bar.setWordWrap(True)
        self.warn_bar.hide()
        layout.addWidget(self.warn_bar)

        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        layout.addWidget(self.splitter)
        self.splitter.addWidget(self._build_week_view())
        self.splitter.addWidget(self._build_todo_view())
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)

        self._watcher = QFileSystemWatcher(self)
        self._watch_files()
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_dir_changed)

        self._render_all()

    # ---- 界面构建 ----

    def _build_week_view(self) -> QWidget:
        """上段: 翻周导航 + 按时间排布的周历网格(横向滚动)."""
        panel = QWidget(self.splitter)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        nav = QHBoxLayout()
        prev_btn = QPushButton("<", panel)
        prev_btn.setFixedWidth(32)
        next_btn = QPushButton(">", panel)
        next_btn.setFixedWidth(32)
        self.today_btn = QPushButton("回到今天", panel)
        self.import_btn = QPushButton("导入课程...", panel)
        self.week_label = QLabel("", panel)
        self.week_label.setObjectName("dayhead")
        prev_btn.clicked.connect(lambda: self._shift_week(-1))
        next_btn.clicked.connect(lambda: self._shift_week(1))
        self.today_btn.clicked.connect(self._go_today)
        self.import_btn.clicked.connect(self._import_courses)
        nav.addWidget(prev_btn)
        nav.addWidget(self.week_label)
        nav.addWidget(next_btn)
        nav.addStretch(1)
        nav.addWidget(self.today_btn)
        nav.addWidget(self.import_btn)
        layout.addLayout(nav)

        self.week_grid = WeekGridWidget(panel)
        self.week_scroll = QScrollArea(panel)
        self.week_scroll.setWidget(self.week_grid)
        self.week_scroll.setWidgetResizable(True)
        layout.addWidget(self.week_scroll)
        return panel

    def _build_todo_view(self) -> QWidget:
        """下段: 新建按钮 + 待办树."""
        panel = QWidget(self.splitter)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        add_btn = QPushButton("新建待办", panel)
        add_btn.clicked.connect(self._add_todo)
        row.addWidget(add_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self.todo_tree = QTreeWidget(panel)
        self.todo_tree.setHeaderLabels(["事项", "截止", "课程"])
        self.todo_tree.setRootIsDecorated(False)
        self.todo_tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.todo_tree)
        return panel

    # ---- 渲染 ----

    def _render_all(self) -> None:
        """以快照全量重渲染周历/待办/警告条."""
        snapshot = self.controller.schedule_snapshot()
        self._render_week(snapshot)
        self._render_todos(snapshot)
        if snapshot.warnings:
            self.warn_bar.setText(
                "日程文件有无法解析的条目: " + "; ".join(snapshot.warnings))
            self.warn_bar.show()
        else:
            self.warn_bar.hide()

    def _render_week(self, snapshot) -> None:
        """渲染当前周: 导航标签与时间网格, 本周时滚动定位到当前时刻附近."""
        self._set_week_label()
        entries = entries_for_week(snapshot, self._monday)
        self.week_grid.set_week(self._monday, entries)
        if self.week_grid.today_column():
            self.week_scroll.verticalScrollBar().setValue(
                max(0, self.week_grid.now_offset() - 140))

    def _set_week_label(self) -> None:
        """导航行文本: 日期区间, 配置学期起点时附第几周, 学期外显示假期."""
        end = self._monday + timedelta(days=6)
        text = f"{self._monday.strftime('%m/%d')} - {end.strftime('%m/%d')}"
        start = self.controller.semester_start()
        if start:
            week = semester_week(self._monday, start)
            count = self.controller.semester_week_count()
            if week < 1 or (count and week > count):
                text += " · 假期"
            else:
                text += f" · 第 {week} 周"
        self.week_label.setText(text)

    def _render_todos(self, snapshot) -> None:
        """重建待办树. blockSignals 防止 setCheckState 触发 itemChanged."""
        today = date.today()
        self.todo_tree.blockSignals(True)
        self.todo_tree.clear()
        for todo in sorted_todos(snapshot.todos):
            status = todo_status(todo, today)
            item = QTreeWidgetItem([todo.title, "", todo.course])
            item.setCheckState(0, Qt.CheckState.Checked if todo.done
                               else Qt.CheckState.Unchecked)
            item.setData(0, ROLE_TID, todo.tid)
            if todo.due is not None:
                due_text = todo.due.strftime("%m-%d")
                if status == "overdue":
                    due_text += " · 逾期"
                    item.setForeground(1, QBrush(QColor(theme.DANGER)))
                elif status == "today":
                    due_text += " · 今天"
                item.setText(1, due_text)
            if status == "done":
                font = QFont(item.font(0))
                font.setStrikeOut(True)
                item.setFont(0, font)
                item.setForeground(0, QBrush(QColor(theme.FG_DIM)))
            self.todo_tree.addTopLevelItem(item)
        self.todo_tree.blockSignals(False)

    # ---- 交互 ----

    def _shift_week(self, delta: int) -> None:
        """翻周; 学期周数已配置时夹在学期内, 日期溢出忽略本次."""
        start = self.controller.semester_start()
        try:
            target = self._monday + timedelta(weeks=delta)
            if start:
                target = clamp_to_semester(
                    target, start, self.controller.semester_week_count())
            self._monday = target
        except OverflowError:
            return
        self._render_all()

    def _go_today(self) -> None:
        """回到今天所在周."""
        self._monday = week_start(date.today())
        self._render_all()

    def _add_todo(self) -> None:
        """对话框新建待办并写回."""
        dialog = TodoDialog(self.controller.course_names(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, due, course = dialog.values()
        self._mutate(lambda: self.controller.add_todo(title, due, course))

    def _on_item_changed(self, item, _column) -> None:
        """勾选状态变化 -> toggle_todo 写回."""
        tid = item.data(0, ROLE_TID)
        if tid is None:
            return
        self._mutate(lambda: self.controller.toggle_todo(tid))

    def _import_courses(self) -> None:
        """从个人校历 .html 导入课程: 选文件 -> 确认替换 courses 段 -> 写回."""
        path, _selected = QFileDialog.getOpenFileName(
            self, "选择个人校历页面", "", "个人校历 (*.html *.htm)")
        if not path:
            return
        current = self.controller.schedule_snapshot()
        answer = QMessageBox.question(
            self, "导入课程",
            f"将用个人校历替换课程数据(现 {len(current.courses)} 条),\n"
            "手动事件与待办不受影响. 继续?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._self_write = True
        result = self.controller.import_courses(path)
        if not result.ok:
            self._self_write = False
            _warn(self, "导入失败", result.message)
        elif result.detail.get("warnings"):
            _warn(self, "导入完成(部分条目有提示)",
                  result.message + "\n\n" + "\n".join(result.detail["warnings"]))
        self._render_all()

    def _mutate(self, action) -> None:
        """写回动作统一入口: 抑制自写触发的 watcher, 完成后刷新.

        标志只在下一次 fileChanged 消费一次; 保存失败时立即清除.
        """
        self._self_write = True
        result = action()
        if not result.ok:
            self._self_write = False
            _warn(self, "日程操作失败", result.message)
        self._render_all()

    # ---- 文件监听 ----

    def _watch_files(self) -> None:
        """(重新)挂载日程文件与父目录监听."""
        path = self.controller.schedule_path
        watch_dir = os.path.dirname(path) or "."
        watched = self._watcher.files() + self._watcher.directories()
        for target in (path, watch_dir):
            if target not in watched:
                self._watcher.addPath(target)

    def _on_file_changed(self, path: str) -> None:
        """文件内容变化: 自写被抑制一次, 其余直接重载."""
        if self._self_write:
            self._self_write = False
        else:
            self._render_all()
        if path not in self._watcher.files():
            self._watcher.addPath(path)  # 原子替换后旧监听失效, 重挂

    def _on_dir_changed(self, _path: str) -> None:
        """目录变化(文件被删/替换): 清抑制标志, 重挂监听并重载(缺失会重播种)."""
        self._self_write = False
        self._watch_files()
        self._render_all()
