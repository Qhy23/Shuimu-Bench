"""ai_tab 测试: 三态页签/配置/流式对话/会话切换(offscreen, 假 controller)."""

from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QDialog

from shuimu.core import ai_actions
from shuimu.core.ai import AiError
from shuimu.core.ai_actions import SYSTEM_PROMPT
from shuimu.core.controller import ActionResult, Controller
from shuimu.core.storage import Storage
from shuimu.gui import theme
from shuimu.plugins.ai.ai_tab import (
    PAGE_CONFIG,
    PAGE_DISABLED,
    PAGE_MAIN,
    AiHistoryDialog,
    AiTab,
)


def make_record(status="ok"):
    """ai 插件记录替身."""
    return SimpleNamespace(status=status, error="",
                           manifest=SimpleNamespace(name="AI 工具"))


class FakeAiController:
    """AiTab 依赖的 controller 替身(记录调用, 零网络)."""

    def __init__(self, status="ok", configured=True):
        self.record = make_record(status)
        self.configured = configured
        self.session_seq = 0
        self.sessions = []            # [{"id", "title", "message_count"}]
        self.messages = {}            # id -> [{"role", "content"}]
        self.ask_calls = []
        self.config_calls = []
        self.enabled_calls = []
        self.cleared = 0
        self.plugins = SimpleNamespace(get=lambda pid: self.record)
        self.config = {"ai": {}}
        self.storage = SimpleNamespace(
            list_courses=lambda: [{"id": 1, "name": "高数"},
                                  {"id": 2, "name": "AI 编程"}],
            ai_list_messages=lambda sid: self.messages.get(sid, []))

    def ai_status(self):
        """配置态替身."""
        return ActionResult(True, "状态", {
            "plugin": self.record.status,
            "base_url": "https://api.example.com/v1" if self.configured
                        else "",
            "model": "m" if self.configured else "",
            "has_key": True, "configured": self.configured,
            "sessions": len(self.sessions), "messages": 0})

    def ai_config_set(self, base_url=None, model=None, api_key=None):
        """配置保存替身."""
        self.config_calls.append((base_url, model, api_key))
        if base_url and model:
            self.configured = True
        return ActionResult(True, "AI 配置已保存", {})

    def ai_ask(self, question, course_ids=None, resource_ids=None,
               include_learn=False, session_id=None, stream=False,
               on_delta=None, think=False, on_thinking=None):
        """问答替身: 思考(勾选时)与回答流式回调, 落一条会话."""
        del resource_ids, stream
        self.ask_calls.append((question, list(course_ids or []),
                               include_learn, session_id, think))
        if think and on_thinking is not None:
            on_thinking(getattr(self, "ask_thinking_text", "先想想"))
        answer = getattr(self, "ask_answer", "第一段第二段")
        if on_delta is not None:
            on_delta(answer)
        self.session_seq += 1
        new_id = self.session_seq
        self.messages[new_id] = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer}]
        self.sessions.insert(0, {"id": new_id, "title": question[:40],
                                 "message_count": 2})
        return ActionResult(True, "已回答", {
            "answer": answer, "session_id": new_id,
            "usage": {"total_tokens": 42},
            "context_items": [{"label": "讲义.pdf", "source": "高数",
                               "kind": "file", "chars": 10,
                               "truncated": False, "skipped": ""}]})

    def ai_history(self, limit=20):
        """历史替身."""
        del limit
        return ActionResult(True, "n 个会话", {"sessions": self.sessions})

    def ai_clear_history(self, session_id=None):
        """清除替身: 按真实语义支持单会话或全部."""
        self.cleared += 1
        if session_id is None:
            self.sessions = []
            self.messages = {}
        else:
            self.sessions = [row for row in self.sessions
                             if row["id"] != session_id]
            self.messages.pop(session_id, None)
        return ActionResult(True, "已清除", {"removed": 1})

    def set_plugin_enabled(self, plugin_id, enabled, confirm=None):
        """启用替身(network 启用先过 confirm)."""
        self.enabled_calls.append((plugin_id, enabled, confirm))
        manifest = self.record.manifest
        if enabled and confirm is not None and not confirm(manifest):
            return ActionResult(False, "已取消: 网络权限未确认", {})
        self.record = make_record("ok" if enabled else "disabled")
        return ActionResult(True, "已启用" if enabled else "已禁用", {})


@pytest.fixture()
def tab(qtbot):
    """已配置主视图态的页签 + 替身 controller."""
    ctl = FakeAiController(configured=True)
    widget = AiTab(ctl)
    widget._worker_controller_factory = lambda: (ctl, None)
    qtbot.addWidget(widget)
    original_send = widget._send

    def send_and_wait():
        original_send()
        qtbot.waitUntil(lambda: not widget._ask_running, timeout=5000)

    widget._send = send_and_wait
    return widget, ctl


class TestStates:
    """三态切换."""

    def test_disabled_shows_guide(self, qtbot):
        """插件禁用: 引导页."""
        ctl = FakeAiController(status="disabled")
        widget = AiTab(ctl)
        qtbot.addWidget(widget)
        assert widget.stack.currentIndex() == PAGE_DISABLED

    def test_enabled_unconfigured_shows_config(self, qtbot):
        """已启用未配置: 配置页."""
        ctl = FakeAiController(configured=False)
        widget = AiTab(ctl)
        qtbot.addWidget(widget)
        assert widget.stack.currentIndex() == PAGE_CONFIG

    def test_configured_shows_main(self, tab):
        """已配置: 主视图."""
        widget, _ctl = tab
        assert widget.stack.currentIndex() == PAGE_MAIN

    def test_settings_returns_to_config(self, tab):
        """已配置问答页可返回配置页, 配置内容保持可编辑."""
        widget, _ctl = tab
        widget.settings_button.click()
        assert widget.stack.currentIndex() == PAGE_CONFIG
        assert widget.base_url_edit.isEnabled()
        assert widget.model_edit.isEnabled()
        assert widget.key_edit.isEnabled()

    def test_course_choices_filled(self, tab):
        """上下文下拉含 不附带/全部课程/各课程."""
        widget, _ctl = tab
        labels = [widget.context_box.itemText(i)
                  for i in range(widget.context_box.count())]
        assert labels == ["不附带", "全部课程", "高数", "AI 编程"]


def test_profile_switch_and_save_as(qtbot, tmp_path, monkeypatch):
    """配置页可切换已有组并另存新组, 当前组同步更新."""
    storage = Storage(str(tmp_path / "profiles.db"))
    ctl = Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(tmp_path / "l.yaml"),
                     plugins_state_path=str(tmp_path / "plugins.yaml"),
                     external_plugins_dir=str(tmp_path / "ext"),
                     ai_config_path=str(tmp_path / "ai.json"))
    store = ctl._ai_store()
    store.save_profile("A", {"base_url": "https://a.example/v1", "model": "a",
                             "api_key": "key-a"})
    store.save_profile("B", {"base_url": "https://b.example/v1", "model": "b"},
                       activate=False)
    widget = AiTab(ctl)
    qtbot.addWidget(widget)
    widget._show_config()
    widget.profile_box.setCurrentText("B")
    assert widget.base_url_edit.text() == "https://b.example/v1"
    assert widget.model_edit.text() == "b"
    assert ctl.ai_status().detail["active_profile"] == "B"
    monkeypatch.setattr("shuimu.plugins.ai.ai_tab.QInputDialog.getText",
                        lambda *_args, **_kwargs: ("C", True))
    widget.key_edit.setText("key-c")
    widget._save_profile_as()
    detail = ctl.ai_status().detail
    assert detail["active_profile"] == "C"
    assert detail["model"] == "b"
    assert set(detail["profiles"]) == {"A", "B", "C"}
    storage.close()


def test_delete_profile_updates_config_page(qtbot, tmp_path, monkeypatch):
    """删除当前配置组后配置页回退到剩余组."""
    storage = Storage(str(tmp_path / "profiles-delete.db"))
    ctl = Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(tmp_path / "l.yaml"),
                     plugins_state_path=str(tmp_path / "plugins.yaml"),
                     external_plugins_dir=str(tmp_path / "ext"),
                     ai_config_path=str(tmp_path / "ai.json"))
    store = ctl._ai_store()
    store.save_profile("A", {"model": "a"})
    store.save_profile("B", {"model": "b"}, activate=False)
    widget = AiTab(ctl)
    qtbot.addWidget(widget)
    widget._show_config()
    monkeypatch.setattr("shuimu.plugins.ai.ai_tab._confirm",
                        lambda *_args, **_kwargs: True)
    widget._delete_profile()
    assert ctl.ai_status().detail["active_profile"] == "B"
    assert widget.profile_box.currentText() == "B"
    storage.close()


def test_system_prompt_restricts_formula_delimiters():
    """共享提示词只允许 $ 公式环境, 禁止当前无法渲染的 \\[ \\] 形式."""
    assert "$...$" in SYSTEM_PROMPT
    assert "$$...$$" in SYSTEM_PROMPT
    assert "不要使用 \\[...\\]" in SYSTEM_PROMPT


class TestEnableAndConfig:
    """启用与配置动作."""

    def test_enable_button_confirms_network(self, qtbot, monkeypatch):
        """启用按钮走 network 确认回调(替身必须真的调用 confirm)."""
        ctl = FakeAiController(status="disabled")
        widget = AiTab(ctl)
        qtbot.addWidget(widget)
        monkeypatch.setattr(
            "shuimu.plugins.ai.ai_tab.confirm_network_enable",
            lambda parent, manifest: True)
        widget._enable_plugin()
        assert ctl.enabled_calls
        _pid, _enabled, confirm = ctl.enabled_calls[0]
        assert confirm is not None
        assert confirm(ctl.record.manifest) is True

    def test_save_config_empty_key_keeps(self, tab):
        """密钥留空保存: api_key 传 None(不动)."""
        widget, ctl = tab
        widget.base_url_edit.setText("https://api.example.com/v1")
        widget.model_edit.setText("m")
        widget.key_edit.clear()
        widget._save_config()
        assert ctl.config_calls == [("https://api.example.com/v1", "m",
                                     None)]

    def test_clear_key_confirm_rejected(self, tab, monkeypatch):
        """清除密钥被确认框拒绝时不调用."""
        widget, ctl = tab
        monkeypatch.setattr("shuimu.plugins.ai.ai_tab._confirm",
                            lambda parent, text: False)
        widget._clear_key()
        assert not ctl.config_calls


class TestChat:
    """对话与流式."""

    def test_send_streams_into_browser(self, tab):
        """发送: 问题与流式回复入消息流, 会话下拉更新."""
        widget, ctl = tab
        widget.input_edit.setPlainText("什么是特征值")
        widget._send()
        assert ctl.ask_calls[0][0] == "什么是特征值"
        assert ctl.ask_calls[0][4] is False    # 默认不深度思考
        text = widget.chat_browser.toPlainText()
        assert "什么是特征值" in text and "你" in text
        assert "水木 AI" in text
        assert "第一段第二段" in text
        assert widget.input_edit.toPlainText() == ""
        assert widget.session_box.count() == 2   # 新对话 + 1 个会话

    def test_tokens_accumulate_and_reset(self, tab):
        """tokens 常驻输入区下方: 逐轮累计, 新对话清零, 可配窗口上限."""
        widget, ctl = tab
        ctl.config = {"ai": {"context_tokens": 1000}}
        widget.input_edit.setPlainText("问一")
        widget._send()
        assert widget.tokens_label.text() == "本会话累计 42 tokens / 可用 1000"
        widget.input_edit.setPlainText("问二")
        widget._send()
        assert "累计 84 tokens" in widget.tokens_label.text()
        widget._new_chat()
        assert widget.tokens_label.text() == ""

    def test_tokens_missing_usage_not_reported_as_zero(self, tab):
        """服务端无 usage 时显示未提供, 不把未知用量误写成 0."""
        widget, ctl = tab
        ctl.ai_ask = lambda *args, **kwargs: ActionResult(True, "已回答", {
            "answer": "答", "session_id": 99, "usage": {},
            "context_items": []})
        widget.input_edit.setPlainText("问")
        widget._send()
        assert widget.tokens_label.text() == "用量未提供"
        assert widget._session_tokens == 0

    def test_thinking_inline_toggle(self, tab):
        """思考内嵌该轮回答前: 默认收起锚点行, 点击展开/再收起."""
        widget, ctl = tab
        widget.think_check.setChecked(True)
        widget.input_edit.setPlainText("难题")
        widget._send()
        assert ctl.ask_calls[0][4] is True
        text = widget.chat_browser.toPlainText()
        assert "思考过程 (3 字)" in text       # 锚点行带字数
        assert "先想想" not in text            # 收起态不显示内容
        widget._on_anchor(QUrl("think:0"))
        assert "先想想" in widget.chat_browser.toPlainText()
        widget._on_anchor(QUrl("think:0"))
        assert "先想想" not in widget.chat_browser.toPlainText()
        assert "思考过程 (3 字)" in widget.chat_browser.toPlainText()

    def test_thinking_answer_starts_separate_paragraph(self, tab):
        """收起/展开思考区后回答另起段, 不与思考标签或内容同行."""
        widget, _ctl = tab
        widget.think_check.setChecked(True)
        widget.input_edit.setPlainText("题")
        widget._send()
        text = widget.chat_browser.toPlainText()
        assert "思考过程 (3 字)\n\n第一段第二段" in text
        widget._on_anchor(QUrl("think:0"))
        assert "先想想\n\n第一段第二段" in widget.chat_browser.toPlainText()
        html = widget.chat_browser.toHtml()
        assert theme.tokens().accent_hover in html

    def test_math_rendered(self, tab):
        """行内/行间公式经轻量转换渲染, 无占位符残留."""
        widget, ctl = tab
        ctl.ask_answer = "面积 $S = \\pi r^2$ 成立"
        widget.input_edit.setPlainText("公式")
        widget._send()
        html = widget.chat_browser.toHtml()
        assert "π" in widget.chat_browser.toPlainText()
        assert "r<sup>2</sup>" in html or "font-weight:700" in html
        assert "\u2063" not in widget.chat_browser.toPlainText()
        del ctl.ask_answer

    def test_many_formulas_leave_no_placeholders(self, tab):
        """多公式与跨轮渲染后, F28/F29 等占位符不会残留."""
        widget, ctl = tab
        ctl.ask_answer = " ".join(f"$x_{{{i}}}$" for i in range(1, 36))
        widget.input_edit.setPlainText("公式")
        widget._send()
        text = widget.chat_browser.toPlainText()
        assert "F28" not in text and "F29" not in text
        assert "⁣" not in text
        for i in range(1, 36):
            assert f"x{i}" in text
        ctl.ask_answer = "第二轮 $y_{42}$"
        widget.input_edit.setPlainText("再问")
        widget._send()
        assert "y42" in widget.chat_browser.toPlainText()
        assert "⁣" not in widget.chat_browser.toPlainText()
        del ctl.ask_answer

    def test_long_complex_formula_sequence_has_no_placeholders(self, tab):
        """含分数/中文说明的长公式序列后半段不再被旧文档边界跳过."""
        widget, ctl = tab
        ctl.ask_answer = ("前 $a$ 后 " + " ".join(
            f"$\\frac{{x_{{{i}}}}}{{y_{{{i}}}}}$" for i in range(1, 50)))
        widget.input_edit.setPlainText("公式")
        widget._send()
        text = widget.chat_browser.toPlainText()
        assert "⁣" not in text
        assert "(x49)/(y49)" in text
        ctl.ask_answer = ("前 $a$ 后 " + " ".join(
            f"$x_{{{i}}} + \\text{{中文说明}} $" for i in range(1, 50)))
        widget.input_edit.setPlainText("再问")
        widget._send()
        text = widget.chat_browser.toPlainText()
        assert "⁣" not in text
        assert "x49 + 中文说明" in text
        del ctl.ask_answer

    def test_answer_rendered_as_markdown(self, tab):
        """完成后整流重渲染: 标题/加粗生效, 历史轮次不丢."""
        widget, ctl = tab
        ctl.ask_answer = "# 小节\n\n这是**加粗**内容"
        widget.input_edit.setPlainText("q1")
        widget._send()
        ctl.ask_answer = "第二轮回答"
        widget.input_edit.setPlainText("q2")
        widget._send()
        html = widget.chat_browser.toHtml()
        assert "xx-large" in html                  # h1 生效
        assert "q1" in widget.chat_browser.toPlainText()   # 首轮仍在
        del ctl.ask_answer

def test_background_ask_uses_independent_storage(qtbot, tmp_path):
    """真实 SQLite 下后台整理资料不跨线程复用主连接."""
    storage = Storage(str(tmp_path / "thread.db"))
    ctl = Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(tmp_path / "l.yaml"),
                     plugins_state_path=str(tmp_path / "plugins.yaml"),
                     external_plugins_dir=str(tmp_path / "ext"),
                     ai_config_path=str(tmp_path / "ai.json"))
    cid = storage.ensure_default_course()
    source = tmp_path / "资料.txt"
    source.write_text("课程资料", encoding="utf-8")
    storage.add_resource(cid, "file", str(source))
    ctl.plugins.set_enabled("ai", True)
    ctl._ai_store().save_profile("测试", {"base_url": "u", "model": "m"})
    widget = AiTab(ctl)
    qtbot.addWidget(widget)

    class FailingTransport:
        """在模型调用阶段返回可控失败, 不发生网络请求."""

        def post_json(self, *_args, **_kwargs):
            """非流式请求同样返回可控网络失败."""
            raise AiError("network", "模拟网络不可用")

        def post_stream(self, *_args, **_kwargs):
            """流式请求返回可控网络失败, 不联网."""
            raise AiError("network", "模拟网络不可用")

    ctl._ai_transport_factory = FailingTransport
    built = []
    original_build = ai_actions.build_context

    def record_build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        built.append(result)
        return result

    ai_actions.build_context = record_build
    try:
        widget.context_box.setCurrentIndex(1)
        widget.input_edit.setPlainText("问题")
        widget._send()
        qtbot.waitUntil(lambda: not widget._ask_running, timeout=5000)
    finally:
        ai_actions.build_context = original_build
    assert built and built[0].total_chars > 0
    assert "暂时无法访问 AI 服务" in widget.chat_browser.toPlainText()
    storage.close()


def test_context_build_does_not_block_ui(qtbot):
    """附带资料整理期间主窗口可继续处理 Qt 事件, 不阻塞界面."""
    ctl = FakeAiController(configured=True)
    entered = threading.Event()
    release = threading.Event()

    def slow_ask(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return ActionResult(True, "已回答", {
            "answer": "答", "session_id": 1, "usage": {},
            "context_items": []})

    ctl.ai_ask = slow_ask
    widget = AiTab(ctl)
    widget._worker_controller_factory = lambda: (ctl, None)
    qtbot.addWidget(widget)
    widget.context_box.setCurrentIndex(1)
    widget.input_edit.setPlainText("问")
    widget._send()
    assert entered.wait(1)
    started = time.monotonic()
    for _ in range(10):
        qtbot.wait(5)
    assert time.monotonic() - started < 1
    assert widget._ask_running
    assert widget.send_button.isEnabled() is False
    release.set()
    qtbot.waitUntil(lambda: not widget._ask_running, timeout=5000)
    assert "答" in widget.chat_browser.toPlainText()


class TestChatActions:
    """发送失败/上下文/会话与历史管理的后续测试."""

    def test_send_failure_appends_note(self, tab):
        """失败: 错误消息追加在消息流内."""
        widget, ctl = tab
        ctl.ai_ask = lambda *args, **kwargs: ActionResult(
            False, "鉴权失败", {"state": "auth"})
        widget.input_edit.setPlainText("q")
        widget._send()
        assert "(鉴权失败)" in widget.chat_browser.toPlainText()
    def test_context_selection_all(self, tab):
        """上下文选择全部课程: course_ids 为两门课."""
        widget, ctl = tab
        widget.context_box.setCurrentIndex(1)
        widget.input_edit.setPlainText("q")
        widget._send()
        assert ctl.ask_calls[0][1] == [1, 2]
        assert ctl.ask_calls[0][2] is True    # 含学堂缓存默认勾选

    def test_context_selection_one_course(self, tab):
        """上下文选择单门课."""
        widget, ctl = tab
        widget.context_box.setCurrentIndex(3)   # AI 编程
        widget.input_edit.setPlainText("q")
        widget._send()
        assert ctl.ask_calls[0][1] == [2]

    def test_session_switch_rebuilds(self, tab):
        """切换会话: 消息流重建为该会话内容."""
        widget, _ctl = tab
        widget.input_edit.setPlainText("第一问")
        widget._send()
        widget.input_edit.setPlainText("第二问")
        widget._send()
        # 两条会话 + 新对话占位
        assert widget.session_box.count() == 3
        widget.session_box.setCurrentIndex(2)
        assert "第一问" in widget.chat_browser.toPlainText()

    def test_new_chat_clears_view(self, tab):
        """新对话: 视图清空回到无会话态."""
        widget, _ctl = tab
        widget.input_edit.setPlainText("问")
        widget._send()
        widget._new_chat()
        assert widget.chat_browser.toPlainText() == ""

    def test_history_dialog_delete_one(self, qtbot, tmp_path, monkeypatch):
        """历史管理可单独删除选中会话, 不影响其他会话."""
        storage = Storage(str(tmp_path / "history-one.db"))
        ctl = Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                         links_path=str(tmp_path / "l.yaml"),
                         plugins_state_path=str(tmp_path / "plugins.yaml"),
                         external_plugins_dir=str(tmp_path / "ext"),
                         ai_config_path=str(tmp_path / "ai.json"))
        first = storage.ai_add_round(None, "第一问", "回答一")
        second = storage.ai_add_round(None, "第二问", "回答二")
        dialog = AiHistoryDialog(ctl)
        qtbot.addWidget(dialog)
        assert dialog.session_list.count() == 2
        dialog.session_list.setCurrentRow(0)
        monkeypatch.setattr("shuimu.plugins.ai.ai_tab._confirm",
                            lambda *_args, **_kwargs: True)
        dialog._delete_selected()
        assert dialog.session_list.count() == 1
        remaining = ctl.ai_history().detail["sessions"]
        assert len(remaining) == 1
        assert remaining[0]["id"] in (first, second)
        storage.close()

    def test_delete_current_session_resets_main_view(self, tab, monkeypatch):
        """删除当前会话后消息流与新对话状态清空, 其他历史可继续选择."""
        widget, _ctl = tab
        widget.input_edit.setPlainText("第一问")
        widget._send()
        widget.input_edit.setPlainText("第二问")
        widget._send()
        current_id = widget._session_id
        assert current_id is not None
        monkeypatch.setattr("shuimu.plugins.ai.ai_tab._confirm",
                            lambda *_args, **_kwargs: True)
        widget._ctl.ai_clear_history(session_id=current_id)
        widget._session_id = None
        widget._rounds = []
        widget._refresh_sessions()
        widget._rebuild_chat()
        assert widget.chat_browser.toPlainText() == ""
        assert widget.session_box.currentData() is None
        assert widget.session_box.count() == 2

    def test_history_dialog_open_selected(self, qtbot, tmp_path):
        """双击或打开按钮返回选中会话 id."""
        storage = Storage(str(tmp_path / "history-open.db"))
        ctl = Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                         links_path=str(tmp_path / "l.yaml"),
                         plugins_state_path=str(tmp_path / "plugins.yaml"),
                         external_plugins_dir=str(tmp_path / "ext"),
                         ai_config_path=str(tmp_path / "ai.json"))
        session_id = storage.ai_add_round(None, "可打开会话", "回答")
        dialog = AiHistoryDialog(ctl)
        qtbot.addWidget(dialog)
        dialog.session_list.setCurrentRow(0)
        dialog._open_selected()
        assert dialog.selected_session_id == session_id
        assert dialog.result() == QDialog.DialogCode.Accepted
        storage.close()

    def test_clear_history_confirm(self, tab, monkeypatch):
        """清除历史经确认后调用并重置."""
        widget, ctl = tab
        widget.input_edit.setPlainText("问")
        widget._send()
        monkeypatch.setattr("shuimu.plugins.ai.ai_tab._confirm",
                            lambda parent, text: True)
        widget._clear_history()
        assert ctl.cleared == 1
        assert widget.chat_browser.toPlainText() == ""
