"""右栏"AI"页签: 插件启用引导 / 配置表单 / 对话主视图三态.

对话/配置全部经 controller.ai_* 动作; 构造与页签切换零联网(只读
配置文件与本地库). 流式回复为同步调用 + 增量追加 + processEvents
(learn 页同款取舍, 线程化留后续).

消息流按轮次结构(self._rounds)整体渲染: 每轮 = 问题 + 思考折叠区 +
回答 + 脚注; 思考以锚点链接内嵌在该轮回答之前(主流 AI 形态), 点击
展开/收起后整流重建; 思考与回答流式期间按纯文本追加, 本轮完成后
整流重渲染为 Markdown(逐块 insertMarkdown 会丢段落), 数学公式经
core/ai_math 轻量转换(占位符 -> LaTeX 子集 HTML). 思考不持久化,
历史回放只含问答本身.
"""

import html as html_mod

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

from shuimu.core.ai_math import extract_formulas, latex_to_html
from shuimu.core.controller import Controller
from shuimu.gui import theme
from shuimu.gui.dialogs import confirm_network_enable, warn

PAGE_DISABLED, PAGE_CONFIG, PAGE_MAIN = 0, 1, 2
CONTEXT_ALL = "all"      # 上下文下拉"全部课程"的 data 值

USER_HEAD = '<p style="color:{primary};"><b>我</b></p>'
AI_HEAD = '<p><b>AI</b></p>'
THINK_TOGGLE = ('<p style="color:{dim};font-size:small">'
                '<a href="{href}">{text}</a></p>')
THINK_BODY = '<p style="color:{dim};font-size:small">{text}</p>'
FOOTNOTE = '<p style="color:{dim};font-size:small">— {text}</p>'
FAILED_NOTE = '<p style="color:{danger};">({text})</p>'


def _confirm(parent, text: str) -> bool:
    """确认框(独立成函数便于测试替换)."""
    answer = QMessageBox.question(parent, "确认", text)
    return answer == QMessageBox.StandardButton.Yes


def _new_round(question: str, thinking: str = "", answer: str = "",
               footnote: str = "", failed: bool = False) -> dict:
    """一轮对话的渲染结构."""
    return {"question": question, "thinking": thinking, "answer": answer,
            "footnote": footnote, "failed": failed,
            "thinking_open": False}


class AiTab(QWidget):
    """右栏 AI 页签: 三态 + 对话视图."""

    status_message = Signal(str)

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self._ctl = ctl
        self._session_id = None      # None = 新对话
        self._session_tokens = 0     # 本次进入会话以来的累计 tokens
        self._rounds = []            # 当前会话的轮次渲染结构
        self._thinking_parts = []    # 本轮流式的思考增量缓存
        self._thinking_shown = False # 本轮流式是否已显示过思考段
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
        self.think_check = QCheckBox("深度思考")
        self.think_check.setToolTip("回答前先深度思考(更慢, 消耗更多 tokens)")
        context.addWidget(self.think_check)
        column.addLayout(context)
        # 消息流
        self.chat_browser = QTextBrowser()
        self.chat_browser.setOpenExternalLinks(False)
        self.chat_browser.anchorClicked.connect(self._on_anchor)
        column.addWidget(self.chat_browser, 1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.tokens().fg_dim};")
        column.addWidget(self.status_label)
        # 输入区
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("输入问题, Ctrl+Enter 发送")
        self.input_edit.setFixedHeight(72)
        column.addWidget(self.input_edit)
        send_row = QHBoxLayout()
        self.tokens_label = QLabel("")
        self.tokens_label.setStyleSheet(f"color: {theme.tokens().fg_dim};")
        send_row.addWidget(self.tokens_label, 1)
        self.send_button = QPushButton("发送")
        self.send_button.clicked.connect(self._send)
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

    def _load_rounds_from_storage(self) -> None:
        """按当前会话从本地库重建轮次(思考不持久化, 恒空)."""
        self._rounds = []
        if self._session_id is None:
            return
        for row in self._ctl.storage.ai_list_messages(self._session_id):
            if row["role"] == "user":
                self._rounds.append(_new_round(row["content"]))
            elif self._rounds:
                self._rounds[-1]["answer"] = row["content"]

    def _rebuild_chat(self) -> None:
        """整流重建消息流(唯一的富文本渲染入口)."""
        self.chat_browser.clear()
        cursor = self.chat_browser.textCursor()
        for index, one_round in enumerate(self._rounds):
            self._render_round(cursor, index, one_round)
        self._scroll_to_end()

    def _render_round(self, cursor: QTextCursor, index: int,
                      one_round: dict) -> None:
        """渲染单轮: 问题 / 思考折叠区 / 回答 / 脚注."""
        if index > 0:
            self._append_html(cursor, "<p><br></p>")
        self._append_html(cursor, USER_HEAD.format(primary=theme.tokens().accent))
        self._insert_markdown_math(cursor, one_round["question"])
        self._append_html(cursor, "<p><br></p>" + AI_HEAD)
        if one_round["thinking"]:
            self._render_thinking(cursor, index, one_round)
        if one_round["failed"]:
            self._append_html(cursor, FAILED_NOTE.format(
                danger=theme.tokens().danger,
                text=html_mod.escape(one_round["answer"])))
        else:
            self._insert_markdown_math(cursor, one_round["answer"])
        if one_round["footnote"]:
            self._append_html(cursor, "<p><br></p>" + FOOTNOTE.format(
                dim=theme.tokens().fg_dim,
                text=html_mod.escape(one_round["footnote"])))

    def _render_thinking(self, cursor: QTextCursor, index: int,
                         one_round: dict) -> None:
        """思考折叠区: 锚点行 + (展开时)灰色正文, 内嵌于回答之前."""
        thinking = one_round["thinking"]
        opened = one_round["thinking_open"]
        marker = "▾" if opened else "▸"
        label = (f"{marker} 思考过程" if opened
                 else f"{marker} 思考过程 ({len(thinking)} 字)")
        self._append_html(cursor, THINK_TOGGLE.format(
            dim=theme.tokens().fg_dim, href=f"think:{index}", text=label))
        if opened:
            self._append_html(cursor, THINK_BODY.format(
                dim=theme.tokens().fg_dim,
                text=html_mod.escape(thinking).replace("\n", "<br>")))

    def _on_anchor(self, url) -> None:
        """锚点点击: think:N 切换该轮思考展开/收起."""
        target = url.toString()
        if not target.startswith("think:"):
            return
        try:
            index = int(target.split(":", 1)[1])
        except ValueError:
            return
        if 0 <= index < len(self._rounds):
            self._rounds[index]["thinking_open"] = \
                not self._rounds[index]["thinking_open"]
            self._rebuild_chat()

    # ---- 消息流渲染辅助 ----

    @staticmethod
    def _append_html(cursor: QTextCursor, fragment: str) -> None:
        """在光标处插入 HTML 片段."""
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(fragment)

    def _insert_markdown_math(self, cursor: QTextCursor, text: str) -> None:
        """Markdown 渲染 + 数学公式轻量转换.

        公式先替换为占位符再渲染正文, 然后在本轮插入范围内查找
        占位符并替换为 LaTeX 近似 HTML(范围用文档字符数界定,
        textCursor 副本位置不可靠).
        """
        plain, formulas = extract_formulas(text)
        document = self.chat_browser.document()
        scope_start = document.characterCount() - 1
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertMarkdown(plain)
        scope_end = document.characterCount()
        for index, latex in enumerate(formulas):
            finder = QTextCursor(document)
            finder.setPosition(scope_start)
            hit = document.find(f"⁣F{index}⁣", finder)
            if not hit.isNull() and hit.position() <= scope_end:
                hit.insertHtml(latex_to_html(latex))

    def _append_plain(self, piece: str) -> None:
        """流式期间纯文本追加(完成后整流重渲染)."""
        cursor = self.chat_browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(piece)
        QApplication.processEvents()

    # ---- 动作 ----

    def refresh_theme(self) -> None:
        """切换主题后刷新内联取色, 并保留当前对话重渲染."""
        dim = theme.tokens().fg_dim
        for label in (self.status_label, self.tokens_label):
            label.setStyleSheet(f"color: {dim};")
        self._rebuild_chat()

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
        """发送问题; 思考与回答流式纯文本追加, 完成后整流重渲染."""
        question = self.input_edit.toPlainText().strip()
        if not question:
            return
        self.send_button.setEnabled(False)
        self.new_chat_button.setEnabled(False)
        self.status_label.setText("回答中...")
        self.repaint()
        cursor = self.chat_browser.textCursor()
        if self._rounds:
            self._append_html(cursor, "<p><br></p>")
        self._append_html(cursor, USER_HEAD.format(primary=theme.tokens().accent))
        self._append_plain(question)
        self._append_html(cursor, "<p><br></p>" + AI_HEAD)
        self._thinking_parts = []
        self._thinking_shown = False
        self.input_edit.clear()
        result = self._ctl.ai_ask(
            question, course_ids=self._selected_course_ids(),
            include_learn=self.learn_check.isChecked(),
            session_id=self._session_id, stream=True,
            think=self.think_check.isChecked(),
            on_thinking=self._on_thinking,
            on_delta=self._on_delta)
        self.send_button.setEnabled(True)
        self.new_chat_button.setEnabled(True)
        self.status_label.setText("")
        thinking = "".join(self._thinking_parts)
        if not result.ok:
            self._rounds.append(_new_round(question, thinking=thinking,
                                           answer=result.message,
                                           failed=True))
            self._rebuild_chat()
            self.status_message.emit(result.message)
            return
        self._session_id = result.detail["session_id"]
        self._rounds.append(_new_round(
            question, thinking=thinking,
            answer=result.detail.get("answer", ""),
            footnote=self._build_footnote(result.detail)))
        self._update_tokens(result.detail.get("usage") or {})
        self._refresh_sessions()
        self._rebuild_chat()
        skipped = [item["label"] for item in
                   result.detail.get("context_items") or []
                   if item.get("skipped")]
        if skipped:
            self.status_label.setText("未纳入: " + ", ".join(skipped[:5]))

    def _on_delta(self, piece: str) -> None:
        """回答增量: 纯文本追加; 思考段后首个增量前隔开."""
        if self._thinking_shown:
            self._append_plain("\n\n")
            self._thinking_shown = False
        self._append_plain(piece)

    def _on_thinking(self, piece: str) -> None:
        """思考增量: 纯文本先行显示(完成后折叠进锚点行)."""
        if not self._thinking_parts:
            self._append_plain("· 思考\n")
            self._thinking_shown = True
        self._thinking_parts.append(piece)
        self._append_plain(piece)

    def _build_footnote(self, detail: dict) -> str:
        """本轮脚注文本: 上下文统计 + 耗时."""
        items = detail.get("context_items") or []
        elapsed = (detail.get("elapsed_ms") or 0) / 1000
        return (f"上下文 {len(items)} 条 "
                f"{detail.get('context_chars', 0)} 字符 · {elapsed:.1f}s")

    def _update_tokens(self, usage: dict) -> None:
        """更新输入区下方的会话累计用量(可配窗口上限时一并显示)."""
        total = usage.get("total_tokens")
        if isinstance(total, int):
            self._session_tokens += total
        window = int(self._ctl.config.get("ai", {}).get(
            "context_tokens", 0) or 0)
        text = f"本会话累计 {self._session_tokens} tokens"
        if window > 0:
            text += f" / 可用 {window}"
        self.tokens_label.setText(text)

    def _reset_tokens(self) -> None:
        """新对话/切换会话: 累计清零(历史轮次的用量未持久化)."""
        self._session_tokens = 0
        self.tokens_label.setText("")

    def _scroll_to_end(self) -> None:
        """消息流滚动到底部."""
        scrollbar = self.chat_browser.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _new_chat(self) -> None:
        """开始新对话(历史保留, 输入区清空)."""
        self._session_id = None
        self._rounds = []
        self.session_box.setCurrentIndex(0)
        self._rebuild_chat()
        self._reset_tokens()

    def _on_session_picked(self, index: int) -> None:
        """切换会话(下拉 data 为 None 表示新对话)."""
        del index
        session_id = self.session_box.currentData()
        if session_id == self._session_id:
            return
        self._session_id = session_id
        self._load_rounds_from_storage()
        self._rebuild_chat()
        self._reset_tokens()

    def _clear_history(self) -> None:
        """清除全部对话历史(确认框)."""
        if not _confirm(self, "清除全部 AI 对话历史?"):
            return
        result = self._ctl.ai_clear_history()
        self.status_message.emit(result.message)
        self._session_id = None
        self._rounds = []
        self._refresh_sessions()
        self._rebuild_chat()
        self._reset_tokens()
