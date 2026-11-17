"""右栏"AI"页签: 插件启用引导 / 配置表单 / 对话主视图三态.

对话/配置全部经 controller.ai_* 动作; 构造与页签切换零联网(只读
配置文件与本地库). 流式回复为同步调用 + on_delta 内增量追加 +
processEvents(learn 页同款取舍, 线程化留后续).
"""

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from shuimu.core.controller import Controller
from shuimu.gui import theme
from shuimu.gui.dialogs import confirm_network_enable, warn

PAGE_DISABLED, PAGE_CONFIG, PAGE_MAIN = 0, 1, 2
CONTEXT_ALL = "all"      # 上下文下拉"全部课程"的 data 值


def _confirm(parent, text: str) -> bool:
    """确认框(独立成函数便于测试替换)."""
    answer = QMessageBox.question(parent, "确认", text)
    return answer == QMessageBox.StandardButton.Yes


class AiTab(QWidget):
    """右栏 AI 页签: 三态 + 对话视图."""

    status_message = Signal(str)

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self._ctl = ctl
        self._session_id = None      # None = 新对话
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self._build_disabled_page()
        self._build_config_page()
        self._build_main_page()
        self.refresh_state()

    # ---- 三页构建 ----

    def _build_disabled_page(self) -> None:
        """页 0: 未启用(说明 + 启用按钮)."""
        page = QWidget()
        column = QVBoxLayout(page)
        hint = QLabel("AI 工具可把课程资料与学堂缓存作为上下文, "
                      "针对课程问题给出回复.\n"
                      "该插件需要访问网络(OpenAI 兼容服务), 默认禁用.")
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.enable_button = QPushButton("启用 AI 插件")
        self.enable_button.clicked.connect(self._enable_plugin)
        column.addWidget(self.enable_button)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_config_page(self) -> None:
        """页 1: 配置表单(服务地址/模型/密钥)."""
        page = QWidget()
        column = QVBoxLayout(page)
        form = QFormLayout()
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText(
            "https://open.bigmodel.cn/api/paas/v4")
        form.addRow("服务地址:", self.base_url_edit)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("glm-4-flash")
        form.addRow("模型名:", self.model_edit)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("未保存")
        form.addRow("API Key:", self.key_edit)
        column.addLayout(form)
        row = QHBoxLayout()
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self._save_config)
        row.addWidget(self.save_button)
        self.clear_key_button = QPushButton("清除密钥")
        self.clear_key_button.clicked.connect(self._clear_key)
        row.addWidget(self.clear_key_button)
        row.addStretch(1)
        column.addLayout(row)
        note = QLabel("兼容智谱/DeepSeek/Kimi/Ollama 等 OpenAI 兼容服务; "
                      "密钥仅存本机 data/ai-config.json(0o600), 不入留痕.")
        note.setWordWrap(True)
        column.addWidget(note)
        column.addStretch(1)
        self.stack.addWidget(page)

    def _build_main_page(self) -> None:
        """页 2: 对话主视图(工具行/上下文行/消息流/输入区)."""
        page = QWidget()
        column = QVBoxLayout(page)
        # 工具行
        tools = QHBoxLayout()
        self.new_chat_button = QPushButton("新对话")
        self.new_chat_button.clicked.connect(self._new_chat)
        tools.addWidget(self.new_chat_button)
        self.session_box = QComboBox()
        self.session_box.currentIndexChanged.connect(self._on_session_picked)
        tools.addWidget(self.session_box, 1)
        self.clear_history_button = QPushButton("清除历史")
        self.clear_history_button.clicked.connect(self._clear_history)
        tools.addWidget(self.clear_history_button)
        column.addLayout(tools)
        # 上下文行
        context = QHBoxLayout()
        context.addWidget(QLabel("上下文:"))
        self.context_box = QComboBox()
        context.addWidget(self.context_box, 1)
        self.learn_check = QCheckBox("含学堂缓存")
        self.learn_check.setChecked(True)
        context.addWidget(self.learn_check)
        column.addLayout(context)
        # 消息流
        self.chat_browser = QTextBrowser()
        self.chat_browser.setOpenExternalLinks(False)
        column.addWidget(self.chat_browser, 1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.FG_DIM};")
        column.addWidget(self.status_label)
        # 输入区
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("输入问题, Ctrl+Enter 发送")
        self.input_edit.setFixedHeight(72)
        column.addWidget(self.input_edit)
        send_row = QHBoxLayout()
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._send)
        send_row.addStretch(1)
        send_row.addWidget(self.send_button)
        column.addLayout(send_row)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input_edit)
        shortcut.activated.connect(self._send)
        self.stack.addWidget(page)

    # ---- 状态机 ----

    def refresh_state(self) -> None:
        """按插件与配置状态切换页面(零联网)."""
        record = self._ctl.plugins.get("ai")
        if record is None or record.status != "ok":
            self.stack.setCurrentIndex(PAGE_DISABLED)
            return
        result = self._ctl.ai_status()
        detail = result.detail
        self.base_url_edit.setText(detail.get("base_url", ""))
        self.model_edit.setText(detail.get("model", ""))
        self.key_edit.clear()
        self.key_edit.setPlaceholderText(
            "已保存, 留空保持不变" if detail.get("has_key") else "未保存")
        if not detail.get("configured"):
            self.stack.setCurrentIndex(PAGE_CONFIG)
            return
        self._refresh_course_choices()
        self._refresh_sessions()
        self._rebuild_chat()
        self.stack.setCurrentIndex(PAGE_MAIN)

    def _refresh_course_choices(self) -> None:
        """上下文下拉: 不附带 / 全部课程 / 各课程."""
        self.context_box.blockSignals(True)
        self.context_box.clear()
        self.context_box.addItem("不附带", None)
        self.context_box.addItem("全部课程", CONTEXT_ALL)
        for row in self._ctl.storage.list_courses():
            self.context_box.addItem(row["name"], row["id"])
        self.context_box.blockSignals(False)

    def _refresh_sessions(self) -> None:
        """会话下拉重建(保持当前选中)."""
        result = self._ctl.ai_history()
        sessions = result.detail.get("sessions", [])
        self.session_box.blockSignals(True)
        self.session_box.clear()
        self.session_box.addItem("新对话", None)
        for row in sessions:
            self.session_box.addItem(f"#{row['id']} {row['title']}",
                                     row["id"])
        index = self.session_box.findData(self._session_id)
        self.session_box.setCurrentIndex(max(index, 0))
        self.session_box.blockSignals(False)

    def _rebuild_chat(self) -> None:
        """按当前会话重建消息流."""
        if self._session_id is None:
            self.chat_browser.clear()
            return
        parts = []
        for row in self._ctl.storage.ai_list_messages(self._session_id):
            prefix = "我: " if row["role"] == "user" else "AI: "
            parts.append(prefix + row["content"])
        self.chat_browser.setPlainText("\n\n".join(parts))
        self._scroll_to_end()

    # ---- 动作 ----

    def _enable_plugin(self) -> None:
        """启用 AI 插件(经网络权限确认框)."""
        result = self._ctl.set_plugin_enabled(
            "ai", True,
            confirm=lambda manifest: confirm_network_enable(self, manifest))
        if not result.ok:
            warn(self, "未能启用 AI 插件", result.message)
        self.refresh_state()

    def _save_config(self) -> None:
        """保存配置; 密钥留空表示保持不变."""
        api_key = self.key_edit.text().strip()
        result = self._ctl.ai_config_set(
            base_url=self.base_url_edit.text().strip(),
            model=self.model_edit.text().strip(),
            api_key=api_key or None)
        self.status_message.emit(result.message)
        if result.ok:
            self.refresh_state()

    def _clear_key(self) -> None:
        """清除已存密钥(确认框)."""
        if not _confirm(self, "清除已保存的 API Key?"):
            return
        result = self._ctl.ai_config_set(api_key="")
        self.status_message.emit(result.message)
        self.refresh_state()

    def _selected_course_ids(self) -> list:
        """上下文下拉 -> course_ids 列表."""
        data = self.context_box.currentData()
        if data is None:
            return []
        if data == CONTEXT_ALL:
            return [row["id"] for row in self._ctl.storage.list_courses()]
        return [data]

    def _send(self) -> None:
        """发送问题; 流式追加回复."""
        question = self.input_edit.toPlainText().strip()
        if not question:
            return
        self.send_button.setEnabled(False)
        self.new_chat_button.setEnabled(False)
        self.status_label.setText("回答中...")
        self.repaint()
        cursor = self.chat_browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.chat_browser.toPlainText():
            cursor.insertText("\n\n")
        cursor.insertText(f"我: {question}\n\nAI: ")
        self.input_edit.clear()
        result = self._ctl.ai_ask(
            question, course_ids=self._selected_course_ids(),
            include_learn=self.learn_check.isChecked(),
            session_id=self._session_id, stream=True,
            on_delta=self._on_delta)
        self.send_button.setEnabled(True)
        self.new_chat_button.setEnabled(True)
        self.status_label.setText("")
        if not result.ok:
            cursor = self.chat_browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(f"\n\n({result.message})")
            self.status_message.emit(result.message)
            return
        self._session_id = result.detail["session_id"]
        self._refresh_sessions()
        context_items = result.detail.get("context_items") or []
        skipped = [item["label"] for item in context_items
                   if item.get("skipped")]
        if skipped:
            self.status_label.setText(
                f"上下文 {len(context_items)} 条, 未纳入: "
                f"{', '.join(skipped[:5])}")
        self._scroll_to_end()

    def _on_delta(self, piece: str) -> None:
        """流式增量追加(不重绘全文)."""
        cursor = self.chat_browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(piece)
        QApplication.processEvents()

    def _scroll_to_end(self) -> None:
        """消息流滚动到底部."""
        scrollbar = self.chat_browser.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _new_chat(self) -> None:
        """开始新对话(历史保留, 输入区清空)."""
        self._session_id = None
        self.session_box.setCurrentIndex(0)
        self.chat_browser.clear()

    def _on_session_picked(self, index: int) -> None:
        """切换会话(下拉 data 为 None 表示新对话)."""
        del index
        session_id = self.session_box.currentData()
        if session_id == self._session_id:
            return
        self._session_id = session_id
        self._rebuild_chat()

    def _clear_history(self) -> None:
        """清除全部对话历史(确认框)."""
        if not _confirm(self, "清除全部 AI 对话历史?"):
            return
        result = self._ctl.ai_clear_history()
        self.status_message.emit(result.message)
        self._session_id = None
        self._refresh_sessions()
        self._rebuild_chat()
