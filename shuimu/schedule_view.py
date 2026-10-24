"""日程页签: 周历(课表+事件)与待办列表.

上段周历 7 天列(横向滚动, 今天列高亮, 可翻周); 下段待办树(勾选完成/逾期
红色/完成划线置底). 渲染完全由 controller.schedule_snapshot() 全量驱动;
课表与事件在外部编辑, QFileSystemWatcher 自动重载; 待办可 GUI 新建与勾选,
经 Controller 写回 YAML 并留痕.
"""

import os
from datetime import date, timedelta

from PySide6.QtCore import QDate, QFileSystemWatcher, Qt, QTime
from PySide6.QtGui import QBrush, QColor, QCursor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTimeEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .controller import Controller
from .schedule import (
    SESSION_TIMES,
    WEEKDAY_NAMES,
    Event,
    clamp_to_semester,
    entries_for_week,
    expand_weekly,
    sorted_todos,
    todo_status,
    week_label,
    week_start,
)
from .week_grid import WeekGridWidget

SESSION_LABELS = [f"第{i}节 {s.strftime('%H:%M')}-{e.strftime('%H:%M')}"
                  for i, (s, e) in sorted(SESSION_TIMES.items())]


class ManualEntryDialog(QDialog):
    """手动添加/修改日程, 双模式(对齐 thu-info-app): 按周数与节次 / 按日期与时间.

    周数模式把所选周次展开为逐次事件(共享系列号, 由 Controller 指派);
    series_mode 为修改整个系列: 只改标题/地点/起止时间, 日期不可改.
    """

    def __init__(self, semester_start, week_count, parent=None,
                 initial=None, series_mode=False):
        super().__init__(parent)
        self.semester_start = semester_start
        self.week_count = week_count if week_count > 0 else 18
        self.series_mode = series_mode
        self.setWindowTitle("修改日程" if initial else "添加日程")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(self)
        self.title_edit.setPlaceholderText("必填, 如: 组会/自习")
        form.addRow("标题", self.title_edit)
        self.location_edit = QLineEdit(self)
        form.addRow("地点", self.location_edit)
        layout.addLayout(form)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_week_page())
        self.stack.addWidget(self._build_date_page())
        layout.addWidget(self.stack)

        if series_mode:
            self.stack.setCurrentIndex(1)
            self.date_edit.setEnabled(False)
            hint = QLabel("修改将应用到该系列全部场次(各场次日期不变)", self)
            layout.addWidget(hint)
        else:
            mode_row = QHBoxLayout()
            self.mode_group = QButtonGroup(self)
            self.week_mode_btn = QPushButton("按周数与节次", self)
            self.date_mode_btn = QPushButton("按日期和时间", self)
            for btn in (self.week_mode_btn, self.date_mode_btn):
                btn.setCheckable(True)
                self.mode_group.addButton(btn)
            self.week_mode_btn.toggled.connect(
                lambda on: self.stack.setCurrentIndex(0) if on else None)
            self.date_mode_btn.toggled.connect(
                lambda on: self.stack.setCurrentIndex(1) if on else None)
            if not semester_start:
                self.week_mode_btn.setEnabled(False)  # 无学期锚点
                self.week_mode_btn.setToolTip("需在 config.yaml 配置 semester_start")
            (self.date_mode_btn if initial or not semester_start
             else self.week_mode_btn).setChecked(True)
            self.stack.setCurrentIndex(
                1 if initial or not semester_start else 0)
            mode_row.addWidget(self.week_mode_btn)
            mode_row.addWidget(self.date_mode_btn)
            layout.addLayout(mode_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if initial:
            self._prefill(initial)

    # ---- 页面构建 ----

    def _build_week_page(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        self.weekday_box = QComboBox(self)
        self.weekday_box.addItems(WEEKDAY_NAMES)
        form.addRow("星期", self.weekday_box)
        self.session_box = QComboBox(self)
        self.session_box.addItems(SESSION_LABELS + ["自定义"])
        self.session_box.currentIndexChanged.connect(self._on_session)
        form.addRow("节次", self.session_box)
        self.week_start_edit = QTimeEdit(self)
        self.week_end_edit = QTimeEdit(self)
        for edit in (self.week_start_edit, self.week_end_edit):
            edit.setDisplayFormat("HH:mm")
        form.addRow("开始", self.week_start_edit)
        form.addRow("结束", self.week_end_edit)
        self._on_session(0)

        weeks_panel = QWidget(page)
        panel_layout = QVBoxLayout(weeks_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        quick = QHBoxLayout()
        for label, predicate in [("全选", lambda w: True),
                                 ("单周", lambda w: w % 2 == 1),
                                 ("双周", lambda w: w % 2 == 0),
                                 ("清空", lambda w: False)]:
            btn = QPushButton(label, weeks_panel)
            btn.clicked.connect(lambda _c, p=predicate: self._set_weeks(p))
            quick.addWidget(btn)
        quick.addStretch(1)
        panel_layout.addLayout(quick)
        grid = QGridLayout()
        grid.setSpacing(2)
        self.week_buttons = []
        for week in range(1, self.week_count + 1):
            chip = QToolButton(weeks_panel)
            chip.setText(str(week))
            chip.setCheckable(True)
            chip.setFixedWidth(34)
            grid.addWidget(chip, (week - 1) // 8, (week - 1) % 8)
            self.week_buttons.append(chip)
        panel_layout.addLayout(grid)
        form.addRow("周次", weeks_panel)
        return page

    def _build_date_page(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        self.date_edit = QDateEdit(self)
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("日期", self.date_edit)
        self.date_start_edit = QTimeEdit(self)
        self.date_end_edit = QTimeEdit(self)
        for edit in (self.date_start_edit, self.date_end_edit):
            edit.setDisplayFormat("HH:mm")
        self.date_start_edit.setTime(QTime(19, 20))
        self.date_end_edit.setTime(QTime(20, 55))
        form.addRow("开始", self.date_start_edit)
        form.addRow("结束", self.date_end_edit)
        return page

    # ---- 行为 ----

    def _on_session(self, index: int) -> None:
        """选择大节预设时填充起止时间(仍可手动微调)."""
        if 0 <= index < len(SESSION_TIMES):
            start, end = SESSION_TIMES[index + 1]
            self.week_start_edit.setTime(QTime(start.hour, start.minute))
            self.week_end_edit.setTime(QTime(end.hour, end.minute))

    def _set_weeks(self, predicate) -> None:
        """按谓词批量设置周次选择."""
        for week, chip in enumerate(self.week_buttons, 1):
            chip.setChecked(predicate(week))

    def _checked_weeks(self) -> list:
        return [week for week, chip in enumerate(self.week_buttons, 1)
                if chip.isChecked()]

    def _is_date_mode(self) -> bool:
        return self.series_mode or self.date_mode_btn.isChecked()

    def _prefill(self, initial: dict) -> None:
        """修改预填: 进入日期模式并回填字段."""
        self.title_edit.setText(initial["title"])
        self.location_edit.setText(initial["location"])
        for edit in (self.week_start_edit, self.date_start_edit):
            edit.setTime(QTime(initial["start"].hour, initial["start"].minute))
        for edit in (self.week_end_edit, self.date_end_edit):
            end = initial["end"]
            edit.setTime(QTime(end.hour, end.minute))
        if not self.series_mode:
            self.date_edit.setDate(
                QDate(initial["date"].year, initial["date"].month,
                      initial["date"].day))
        self.session_box.setCurrentIndex(len(SESSION_LABELS))  # 自定义

    def _accept_if_valid(self) -> None:
        """校验: 标题非空, 结束晚于开始, 周数模式至少选一周."""
        if not self.title_edit.text().strip():
            _warn(self, "无法保存", "标题不能为空")
            return
        start_edit, end_edit = ((self.date_start_edit, self.date_end_edit)
                                if self._is_date_mode()
                                else (self.week_start_edit, self.week_end_edit))
        if end_edit.time() <= start_edit.time():
            _warn(self, "无法保存", "结束时间必须晚于开始时间, 且在同一天内")
            return
        if not self._is_date_mode() and not self._checked_weeks():
            _warn(self, "无法保存", "请至少选择一个周次")
            return
        self.accept()

    def build_events(self) -> list:
        """返回待写入的事件列表(周数模式为展开后的多次, sid 由 Controller 指派)."""
        title = self.title_edit.text().strip()
        location = self.location_edit.text().strip()
        if self._is_date_mode():
            return [Event(self.date_edit.date().toPython(),
                          self.date_start_edit.time().toPython(),
                          self.date_end_edit.time().toPython(),
                          title, location)]
        return expand_weekly(
            self.semester_start, self._checked_weeks(),
            self.weekday_box.currentIndex() + 1,
            self.week_start_edit.time().toPython(),
            self.week_end_edit.time().toPython(), title, location)


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
        self.add_btn = QPushButton("添加日程...", panel)
        self.week_label = QLabel("", panel)
        self.week_label.setObjectName("dayhead")
        prev_btn.clicked.connect(lambda: self._shift_week(-1))
        next_btn.clicked.connect(lambda: self._shift_week(1))
        self.today_btn.clicked.connect(self._go_today)
        self.import_btn.clicked.connect(self._import_courses)
        self.add_btn.clicked.connect(self._add_manual)
        nav.addWidget(prev_btn)
        nav.addWidget(self.week_label)
        nav.addWidget(next_btn)
        nav.addStretch(1)
        nav.addWidget(self.today_btn)
        nav.addWidget(self.import_btn)
        nav.addWidget(self.add_btn)
        layout.addLayout(nav)

        self.week_grid = WeekGridWidget(panel)
        self.week_grid.cardMenuRequested.connect(self._on_card_menu)
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
        """导航行文本由共享的 week_label 生成(GUI 与 CLI 一致)."""
        self.week_label.setText(week_label(
            self._monday, self.controller.semester_start(),
            self.controller.semester_week_count()))

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

    def _add_manual(self) -> None:
        """手动添加日程: 按周数与节次(展开为系列)或按日期与时间."""
        dialog = ManualEntryDialog(self.controller.semester_start(),
                                   self.controller.semester_week_count(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        events = dialog.build_events()
        self._mutate(lambda: self.controller.add_manual(events))

    def _on_card_menu(self, entry) -> None:
        """卡片右键菜单: 修改/删除, 系列条目提供整组操作."""
        origin = entry.origin
        if origin is None:
            return
        section = "manual" if entry.is_manual else "courses"
        series_count = 0
        if origin.sid:
            series_count = sum(1 for e in getattr(self.controller
                                                  .schedule_snapshot(), section)
                               if e.sid == origin.sid)
        menu = QMenu(self)
        menu.addAction("修改本次...", lambda: self._edit_entry(section, origin, False))
        menu.addAction("删除本次", lambda: self._remove_entry(section, origin, False))
        if series_count > 1:
            menu.addSeparator()
            menu.addAction(f"修改整个系列({series_count} 场)...",
                           lambda: self._edit_entry(section, origin, True))
            menu.addAction(f"删除整个系列({series_count} 场)",
                           lambda: self._remove_entry(section, origin, True))
        menu.exec(QCursor.pos())

    def _edit_entry(self, section: str, origin, whole_series: bool) -> None:
        """修改一次或整个系列: 对话框预填, 写回留痕."""
        initial = {"title": origin.title, "location": origin.location,
                   "date": origin.date, "start": origin.start, "end": origin.end}
        dialog = ManualEntryDialog(self.controller.semester_start(),
                                   self.controller.semester_week_count(),
                                   self, initial=initial,
                                   series_mode=whole_series)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = dialog.build_events()[0]
        self._mutate(lambda: self.controller.update_event(
            section, origin, new, whole_series))

    def _remove_entry(self, section: str, origin,
                      whole_series: bool) -> None:
        """删除一次或整个系列, 带确认."""
        scope = "整个系列(全部场次)" if whole_series else "本次"
        answer = QMessageBox.question(
            self, "删除日程",
            f"确定删除{scope}?\n{origin.title} {origin.date} "
            f"{origin.start.strftime('%H:%M')}")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._mutate(lambda: self.controller.remove_event(
            section, origin, whole_series))

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
