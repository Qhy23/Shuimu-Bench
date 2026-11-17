"""ai_tab 测试: 三态页签/配置/流式对话/会话切换(offscreen, 假 controller)."""

from types import SimpleNamespace

import pytest

from shuimu.core.controller import ActionResult
from shuimu.plugins.ai.ai_tab import (
    PAGE_CONFIG,
    PAGE_DISABLED,
    PAGE_MAIN,
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
               on_delta=None):
        """问答替身: 流式分两块回调, 落一条会话."""
        del resource_ids, stream
        self.ask_calls.append((question, list(course_ids or []),
                               include_learn, session_id))
        if on_delta is not None:
            on_delta("第一段")
            on_delta("第二段")
        self.session_seq += 1
        new_id = self.session_seq
        self.messages[new_id] = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": "第一段第二段"}]
        self.sessions.insert(0, {"id": new_id, "title": question[:40],
                                 "message_count": 2})
        return ActionResult(True, "已回答", {
            "answer": "第一段第二段", "session_id": new_id,
            "context_items": [{"label": "讲义.pdf", "source": "高数",
                               "kind": "file", "chars": 10,
                               "truncated": False, "skipped": ""}]})

    def ai_history(self):
        """历史替身."""
        return ActionResult(True, "n 个会话", {"sessions": self.sessions})

    def ai_clear_history(self, session_id=None):
        """清除替身."""
        del session_id
        self.cleared += 1
        self.sessions = []
        self.messages = {}
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
    qtbot.addWidget(widget)
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

    def test_course_choices_filled(self, tab):
        """上下文下拉含 不附带/全部课程/各课程."""
        widget, _ctl = tab
        labels = [widget.context_box.itemText(i)
                  for i in range(widget.context_box.count())]
        assert labels == ["不附带", "全部课程", "高数", "AI 编程"]


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
        text = widget.chat_browser.toPlainText()
        assert "我: 什么是特征值" in text
        assert "AI: 第一段第二段" in text
        assert widget.input_edit.toPlainText() == ""
        assert widget.session_box.count() == 2   # 新对话 + 1 个会话

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
        assert "我: 第一问" in widget.chat_browser.toPlainText()

    def test_new_chat_clears_view(self, tab):
        """新对话: 视图清空回到无会话态."""
        widget, _ctl = tab
        widget.input_edit.setPlainText("问")
        widget._send()
        widget._new_chat()
        assert widget.chat_browser.toPlainText() == ""

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
