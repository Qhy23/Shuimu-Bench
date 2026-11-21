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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

USER_HEAD = '<p style="color:{primary};"><b>你</b></p>'
AI_HEAD = '<p style="color:{dim};"><b>水木 AI</b></p>'
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


class AiHistoryDialog(QDialog):
    """历史会话管理: 打开/单独删除/清空全部, 不直接改数据库."""

    def __init__(self, ctl: Controller, parent=None):
        super().__init__(parent)
        self._ctl = ctl
        self.selected_session_id = None
        self.setWindowTitle("历史会话")
        self.resize(420, 480)
        layout = QVBoxLayout(self)
        hint = QLabel("选择会话可继续对话, 或仅删除选中的历史.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection)
        self.session_list.currentItemChanged.connect(self._update_buttons)
        self.session_list.itemDoubleClicked.connect(self._open_selected)
        layout.addWidget(self.session_list, 1)
        row = QHBoxLayout()
        self.open_button = QPushButton("打开")
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(self._open_selected)
        row.addWidget(self.open_button)
        self.delete_button = QPushButton("删除选中")
        self.delete_button.setProperty("danger", True)
        self.delete_button.clicked.connect(self._delete_selected)
        row.addWidget(self.delete_button)
        row.addStretch(1)
        layout.addLayout(row)
        bottom = QHBoxLayout()
        clear_button = QPushButton("清空全部...")
        clear_button.setProperty("tool", True)
        clear_button.clicked.connect(self._clear_all)
        bottom.addWidget(clear_button)
        bottom.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setProperty("tool", True)
        close_button.clicked.connect(self.reject)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)
        self.reload()

    def reload(self) -> None:
        """从 controller 重建会话列表, 保留可选中状态."""
        current = self._current_id()
        self.session_list.clear()
        sessions = self._ctl.ai_history(limit=1000).detail.get("sessions", [])
        for row in sessions:
            item = QListWidgetItem(f"#{row['id']}  {row['title']}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(f"{row['message_count']} 条消息")
            self.session_list.addItem(item)
            if row["id"] == current:
                self.session_list.setCurrentItem(item)
        if self.session_list.currentItem() is None and self.session_list.count():
            self.session_list.setCurrentRow(0)
        self._update_buttons()

    def _current_id(self):
        item = self.session_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _update_buttons(self, *_args) -> None:
        """无选中时禁用打开与单删."""
        enabled = self.session_list.currentItem() is not None
        self.open_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _open_selected(self, *_args) -> None:
        """把选中的会话 id 交给调用方打开."""
        if self._current_id() is None:
            return
        self.selected_session_id = self._current_id()
        self.accept()

    def _delete_selected(self) -> None:
        """只删除当前选中会话, 其余历史保留."""
        session_id = self._current_id()
        if session_id is None:
            return
        item = self.session_list.currentItem()
        if not _confirm(self, f"删除会话 {item.text()}?\n此操作不可撤销."):
            return
        result = self._ctl.ai_clear_history(session_id=session_id)
        if result.ok:
            self.reload()

    def _clear_all(self) -> None:
        """清空全部历史, 独立于单删的显式操作."""
        if not _confirm(self, "清空全部 AI 对话历史?\n此操作不可撤销."):
            return
        result = self._ctl.ai_clear_history()
        if result.ok:
            self.session_list.clear()
            self._update_buttons()


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
        self._config_loading = False  # 刷新配置表单时避免下拉触发切换动作
        self.context_box = None
        self.learn_check = None
        self.think_check = None
        self.input_edit = None
        self.tokens_label = None
        self.send_button = None
        self.new_chat_button = None
        self.session_box = None
        self.history_button = None
        self.clear_history_button = None  # 保留给历史管理阶段接线
        self.settings_button = None
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
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("配置组:"))
        self.profile_box = QComboBox()
        self.profile_box.setToolTip("切换当前使用的服务配置")
        self.profile_box.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_box, 1)
        self.save_profile_button = QPushButton("另存为")
        self.save_profile_button.setProperty("tool", True)
        self.save_profile_button.clicked.connect(self._save_profile_as)
        profile_row.addWidget(self.save_profile_button)
        self.delete_profile_button = QPushButton("删除")
        self.delete_profile_button.setProperty("danger", True)
        self.delete_profile_button.clicked.connect(self._delete_profile)
        profile_row.addWidget(self.delete_profile_button)
        column.addLayout(profile_row)
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
        """页 2: 对话主视图(轻量工具行/消息流/上下文/输入卡片)."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(6, 6, 6, 6)
        column.setSpacing(8)
        column.addLayout(self._build_chat_tools(page))
        # 消息流保持纯文本/Markdown 阅读区, 不再叠加重工具栏
        self.chat_browser = QTextBrowser()
        self.chat_browser.setObjectName("aiChatBrowser")
        self.chat_browser.setOpenExternalLinks(False)
        self.chat_browser.anchorClicked.connect(self._on_anchor)
        self.chat_browser.setPlaceholderText(
            "从一个课程问题开始\n\n例如: 帮我梳理这周作业的要求")
        column.addWidget(self.chat_browser, 1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.tokens().fg_dim};")
        self.status_label.setWordWrap(True)
        column.addWidget(self.status_label)
        # 上下文与输入合并为底部工作台, 视觉重心接近 CLI 输入区
        composer = QFrame()
        self._build_composer(composer)
        column.addWidget(composer)
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input_edit)
        shortcut.activated.connect(self._send)
        self.stack.addWidget(page)
        self._refresh_chat_chrome()

    def _build_composer(self, composer) -> None:
        """构建输入卡片: 上下文选项 + 多行输入 + 用量/发送."""
        composer.setObjectName("aiComposer")
        layout = QVBoxLayout(composer)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(5)
        context = QHBoxLayout()
        context.addWidget(QLabel("上下文"))
        self.context_box = QComboBox()
        context.addWidget(self.context_box, 1)
        self.learn_check = QCheckBox("学堂")
        self.learn_check.setChecked(True)
        self.learn_check.setToolTip("包含网络学堂缓存")
        context.addWidget(self.learn_check)
        self.think_check = QCheckBox("思考")
        self.think_check.setToolTip("回答前先深度思考(更慢, 消耗更多 tokens)")
        context.addWidget(self.think_check)
        layout.addLayout(context)
        self.input_edit = QPlainTextEdit()
        self.input_edit.setObjectName("aiInput")
        self.input_edit.setPlaceholderText("询问课程问题…  Ctrl+Enter 发送")
        self.input_edit.setFixedHeight(82)
        layout.addWidget(self.input_edit)
        send_row = QHBoxLayout()
        self.tokens_label = QLabel("")
        self.tokens_label.setStyleSheet(f"color: {theme.tokens().fg_dim};")
        send_row.addWidget(self.tokens_label, 1)
        self.send_button = QPushButton("发送 ↵")
        self.send_button.setProperty("primary", True)
        self.send_button.setToolTip("Ctrl+Enter")
        self.send_button.clicked.connect(self._send)
        send_row.addWidget(self.send_button)
        layout.addLayout(send_row)

    def _build_chat_tools(self, parent) -> QHBoxLayout:
        """轻量工具行: 新对话/历史/当前会话/设置."""
        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.new_chat_button = QPushButton("＋ 新对话", parent)
        self.new_chat_button.setProperty("tool", True)
        self.new_chat_button.clicked.connect(self._new_chat)
        tools.addWidget(self.new_chat_button)
        self.session_box = QComboBox(parent)
        self.session_box.currentIndexChanged.connect(self._on_session_picked)
        self.session_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        tools.addWidget(self.session_box, 1)
        self.history_button = QPushButton("历史", parent)
        self.history_button.setProperty("tool", True)
        self.history_button.clicked.connect(self._show_history)
        tools.addWidget(self.history_button)
        self.settings_button = QPushButton("设置", parent)
        self.settings_button.setToolTip("修改服务地址、模型与 API Key")
        self.settings_button.setProperty("tool", True)
        self.settings_button.clicked.connect(self._show_config)
        tools.addWidget(self.settings_button)
        return tools

    # ---- 状态机 ----
    def refresh_state(self) -> None:
        """按插件与配置状态切换页面(零联网)."""
        record = self._ctl.plugins.get("ai")
        result = self._ctl.ai_status()
        detail = result.detail
        self._config_loading = True
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        profiles = detail.get("profiles", [])
        self.profile_box.addItems(profiles)
        active = detail.get("active_profile", "")
        self.profile_box.setCurrentIndex(max(self.profile_box.findText(active), 0))
        self.profile_box.blockSignals(False)
        self.delete_profile_button.setEnabled(bool(profiles))
        self._config_loading = False
        self.base_url_edit.setText(detail.get("base_url", ""))
        self.model_edit.setText(detail.get("model", ""))
        self.key_edit.clear()
        self.key_edit.setPlaceholderText(
            "已保存, 留空保持不变" if detail.get("has_key") else "未保存")
        if record is None or record.status != "ok":
            self.stack.setCurrentIndex(PAGE_DISABLED)
            return
        if not detail.get("configured"):
            self.stack.setCurrentIndex(PAGE_CONFIG)
            return
        self._refresh_course_choices()
        self._refresh_sessions()
        self._rebuild_chat()
        self.stack.setCurrentIndex(PAGE_MAIN)

    def _show_config(self) -> None:
        """从问答页返回配置表单, 保留当前会话与对话状态."""
        self.stack.setCurrentIndex(PAGE_CONFIG)

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
        self._append_html(cursor, "<p><br></p>")
        self._insert_markdown_math(cursor, one_round["question"])
        self._append_html(cursor, "<p><br></p>" + AI_HEAD.format(
            dim=theme.tokens().fg_dim))
        self._append_html(cursor, "<p><br></p>")
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
        self._refresh_chat_chrome()
        self._rebuild_chat()

    def _refresh_chat_chrome(self) -> None:
        """对话区/输入卡片使用主题面板色, 不覆盖富文本消息着色."""
        tk = theme.tokens()
        self.chat_browser.setStyleSheet(
            f"QTextBrowser#aiChatBrowser {{"
            f"background-color: {tk.base}; border: 1px solid {tk.border};"
            f"border-radius: 6px; padding: 12px; }}"
            f"QTextBrowser#aiChatBrowser a {{color: {tk.accent_hover};}}")
        composer = self.input_edit.parentWidget()
        composer.setStyleSheet(
            f"QFrame#aiComposer {{background-color: {tk.panel};"
            f"border: 1px solid {tk.border}; border-radius: 8px;}}"
            f"QFrame#aiComposer QLabel {{color: {tk.fg_dim};}}")

    def _enable_plugin(self) -> None:
        """启用 AI 插件(经网络权限确认框)."""
        result = self._ctl.set_plugin_enabled(
            "ai", True,
            confirm=lambda manifest: confirm_network_enable(self, manifest))
        if not result.ok:
            warn(self, "未能启用 AI 插件", result.message)
        self.refresh_state()

    def _on_profile_changed(self, index: int) -> None:
        """下拉切换当前配置组并刷新表单(不发出网络请求)."""
        if self._config_loading or index < 0:
            return
        result = self._ctl.ai_profile_switch(self.profile_box.currentText())
        self.status_message.emit(result.message)
        if result.ok:
            self.refresh_state()

    def _save_profile_as(self) -> None:
        """把表单与密钥另存为新配置组并设为当前组."""
        name, accepted = QInputDialog.getText(self, "另存配置组", "配置组名:")
        name = name.strip()
        if not accepted or not name:
            return
        store = self._ctl._ai_store()
        values = {
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_edit.text().strip()}
        new_key = self.key_edit.text().strip()
        if new_key:
            values["api_key"] = new_key
        else:
            _profiles, active = store.load_profiles()
            old_key = _profiles.get(active, {}).get("api_key")
            if old_key:
                values["api_key"] = old_key
        store.save_profile(name, values)
        self.status_message.emit(f"已保存配置组「{name}」")
        self.refresh_state()

    def _delete_profile(self) -> None:
        """删除当前配置组(确认框), 删除后由状态机选择剩余组."""
        name = self.profile_box.currentText()
        if not name or not _confirm(self, f"删除配置组「{name}」及其已存密钥?"):
            return
        result = self._ctl.ai_profile_delete(name)
        self.status_message.emit(result.message)
        if result.ok:
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
        self._append_html(cursor, "<p><br></p>" + AI_HEAD.format(
            dim=theme.tokens().fg_dim))
        self._append_html(cursor, "<p><br></p>")
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

    def _show_history(self) -> None:
        """打开历史管理; 打开会话或删除后同步消息流与下拉."""
        dialog = AiHistoryDialog(self._ctl, self)
        if dialog.exec() == QDialog.DialogCode.Accepted \
                and dialog.selected_session_id is not None:
            self._session_id = dialog.selected_session_id
            self._load_rounds_from_storage()
        else:
            # 删除当前会话后回到新对话, 不保留已删除消息流
            self._session_id = None
            self._rounds = []
        self._refresh_sessions()
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
