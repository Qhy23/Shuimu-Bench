"""controller AI 动作测试: 门禁/配置/问答/历史(假 transport, 零真实联网)."""

import json
import os
import stat

import pytest

from shuimu.core.ai import AiNetworkError, MemoryAiTransport
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage


def write_stub_plugin(tmp_path):
    """外部目录放最小 ai 替身插件(controller 层测试与插件实现解耦)."""
    plugin_dir = tmp_path / "ext-plugins" / "ai"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yaml").write_text(
        "id: ai\nname: AI 替身\nversion: \"0.1.0\"\napi_version: 1\n"
        "description: controller 测试替身\nentry: plugin\n"
        "permissions: [network]\napplies_to: [app]\n", encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(
        "\"\"\"替身: controller 测试不跑插件入口.\"\"\"\n\n\n"
        "def run(job):\n    raise NotImplementedError\n",
        encoding="utf-8")


@pytest.fixture()
def ctl_ai(tmp_path, monkeypatch):
    """AI 动作全隔离的 controller: 返回 (controller, transport, tmp_path).

    外部目录放 ai 替身插件且默认未启用(门禁用例需要), 其余用例自行
    enable(); 配置文件路径指向 tmp; transport 为 MemoryAiTransport.
    """
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-such-template.yaml"))
    write_stub_plugin(tmp_path)
    transport = MemoryAiTransport()
    storage = Storage(str(tmp_path / "ctl.db"))
    controller = Controller(
        storage, schedule_path=str(tmp_path / "s.yaml"),
        links_path=str(tmp_path / "links.yaml"),
        plugins_state_path=str(tmp_path / "plugins.yaml"),
        external_plugins_dir=str(tmp_path / "ext-plugins"),
        ai_transport_factory=lambda: transport,
        ai_config_path=str(tmp_path / "ai-config.json"))
    yield controller, transport, tmp_path
    storage.close()


def enable_ai(controller):
    """启用 ai 插件(network 授权)."""
    controller.plugins.set_enabled("ai", True)


def configure(controller, base_url="https://api.example.com/v1",
              model="test-model", api_key="sk-test"):
    """写入可用配置."""
    controller.ai_config_set(base_url=base_url, model=model,
                             api_key=api_key)


def add_ok_reply(transport, content="答案"):
    """注册一条非流式成功响应."""
    transport.add_json(payload={"choices": [{"message": {"role": "assistant",
                                                          "content": content},
                                              "finish_reason": "stop"}],
                                "usage": {"total_tokens": 5},
                                "model": "test-model"})


class TestAiGate:
    """门禁与配置态."""

    def test_disabled_plugin_no_requests(self, ctl_ai):
        """未启用: 失败且零联网零留痕."""
        controller, transport, _ = ctl_ai
        result = controller.ai_ask("你好")
        assert not result.ok
        assert "未启用" in result.message
        assert not transport.requests

    def test_not_installed(self, tmp_path, monkeypatch):
        """无 ai 插件(内置/外部都指向空目录): 明确提示未安装."""
        monkeypatch.setattr("shuimu.core.controller.template_links_path",
                            lambda: str(tmp_path / "no.yaml"))
        monkeypatch.setattr(
            "shuimu.core.pluginman.builtin_plugins_dir",
            lambda: str(tmp_path / "no-builtin"))
        storage = Storage(str(tmp_path / "d.db"))
        controller = Controller(
            storage, schedule_path=str(tmp_path / "s.yaml"),
            links_path=str(tmp_path / "l.yaml"),
            plugins_state_path=str(tmp_path / "plugins.yaml"),
            external_plugins_dir=str(tmp_path / "ext-empty"),
            ai_config_path=str(tmp_path / "ai.json"))
        result = controller.ai_ask("你好")
        assert not result.ok
        assert "未安装" in result.message
        storage.close()

    def test_ask_without_config(self, ctl_ai):
        """已启用未配置: config 态失败, 零联网零留痕."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        result = controller.ai_ask("你好")
        assert not result.ok
        assert result.detail["state"] == "config"
        assert not transport.requests
        assert all(row["action"] != "ai_ask"
                   for row in controller.storage.list_operations())

    def test_empty_question(self, ctl_ai):
        """空问题拒绝."""
        controller, _, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        result = controller.ai_ask("  ")
        assert not result.ok
        assert result.detail["state"] == "empty"


class TestAiConfig:
    """配置动作."""

    def test_status_unconfigured(self, ctl_ai):
        """status 反映未配置态与密钥有无."""
        controller, _, _ = ctl_ai
        result = controller.ai_status()
        assert result.ok
        assert not result.detail["configured"]
        assert result.detail["plugin"] == "disabled"

    def test_config_set_and_ask_headers(self, ctl_ai):
        """配置写入后请求带 Bearer; ai-config.json 权限 0o600."""
        controller, transport, tmp_path = ctl_ai
        enable_ai(controller)
        configure(controller)
        add_ok_reply(transport)
        result = controller.ai_ask("你好")
        assert result.ok
        headers = transport.requests[0][1]
        assert headers["Authorization"] == "Bearer sk-test"
        config_path = tmp_path / "ai-config.json"
        if os.name != "nt":   # Windows 无 POSIX 权限位
            assert stat.S_IMODE(os.stat(config_path).st_mode) == 0o600

    def test_config_log_has_no_key(self, ctl_ai):
        """配置留痕只记字段名, 无 key 值."""
        controller, _, _ = ctl_ai
        configure(controller, api_key="sk-secret")
        rows = [row for row in controller.storage.list_operations()
                if row["action"] == "ai_config"]
        assert rows
        params = json.loads(rows[0]["params"])
        assert params == {"fields": ["base_url", "model", "api_key"]}
        assert "sk-secret" not in rows[0]["params"]

    def test_switch_profile_changes_active_config(self, ctl_ai):
        """切换配置组后, status 与问答均读取新当前组."""
        controller, transport, _tmp_path = ctl_ai
        store = controller._ai_store()
        store.save_profile("A", {"base_url": "https://a.example/v1",
                                 "model": "model-a", "api_key": "key-a"})
        store.save_profile("B", {"base_url": "https://b.example/v1",
                                 "model": "model-b", "api_key": "key-b"},
                           activate=False)
        result = controller.ai_profile_switch("B")
        assert result.ok
        detail = controller.ai_status().detail
        assert detail["active_profile"] == "B"
        assert detail["model"] == "model-b"
        assert detail["profiles"] == ["A", "B"]
        enable_ai(controller)
        add_ok_reply(transport)
        assert controller.ai_ask("你好").ok
        assert transport.requests[0][1]["Authorization"] == "Bearer key-b"

    def test_delete_active_profile_falls_back(self, ctl_ai):
        """删除当前组后回退到剩余首组."""
        controller, _, _ = ctl_ai
        store = controller._ai_store()
        store.save_profile("A", {"model": "a"})
        store.save_profile("B", {"model": "b"}, activate=False)
        assert controller.ai_profile_delete("A").ok
        assert controller.ai_status().detail["active_profile"] == "B"
        assert not controller.ai_profile_delete("missing").ok

    def test_clear_key(self, ctl_ai):
        """api_key 空串清除密钥, base_url/model 保留."""
        controller, _, tmp_path = ctl_ai
        configure(controller)
        controller.ai_config_set(api_key="")
        stored = json.loads((tmp_path / "ai-config.json").read_text(
            encoding="utf-8"))
        active = stored["active_profile"]
        assert "api_key" not in stored["profiles"][active]
        assert stored["profiles"][active]["model"] == "test-model"


class TestAiAsk:
    """问答全链路."""

    def test_ask_success_and_history(self, ctl_ai):
        """成功: 回答返回, 历史落库两行, 留痕只记元数据."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        add_ok_reply(transport, "线性代数是...")
        result = controller.ai_ask("什么是线性代数")
        assert result.ok
        assert result.detail["answer"] == "线性代数是..."
        session_id = result.detail["session_id"]
        messages = controller.storage.ai_list_messages(session_id)
        assert [row["role"] for row in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "什么是线性代数"
        rows = [row for row in controller.storage.list_operations()
                if row["action"] == "ai_ask"]
        assert len(rows) == 1
        params = json.loads(rows[0]["params"])
        detail = json.loads(rows[0]["detail"])
        assert params["question_head"] == "什么是线性代数"
        assert detail["model"] == "test-model"
        assert "线性代数是" not in rows[0]["detail"]

    def test_ask_with_context(self, ctl_ai, tmp_path):
        """带课程上下文: system 消息含资料文本, context_items 统计."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        note = tmp_path / "note.txt"
        note.write_text("特征值是方程的根", encoding="utf-8")
        cid = controller.storage.ensure_default_course()
        controller.storage.add_resource(cid, "file", str(note))
        add_ok_reply(transport)
        result = controller.ai_ask("特征值是什么", course_ids=[cid])
        assert result.ok
        body = transport.requests[0][2]
        system = body["messages"][0]
        assert system["role"] == "system"
        assert "特征值是方程的根" in system["content"]
        assert body["messages"][-1]["content"] == "特征值是什么"
        assert result.detail["context_chars"] > 0
        assert result.detail["context_items"][0]["label"] == "note.txt"

    def test_ask_multi_round_session(self, ctl_ai):
        """续问: 历史拼进 messages, 同会话四条消息."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        add_ok_reply(transport, "答一")
        add_ok_reply(transport, "答二")
        first = controller.ai_ask("问一")
        session_id = first.detail["session_id"]
        second = controller.ai_ask("问二", session_id=session_id)
        assert second.ok
        body = transport.requests[1][2]
        contents = [m["content"] for m in body["messages"]]
        assert contents == ["问一", "答一", "问二"]
        assert len(controller.storage.ai_list_messages(session_id)) == 4

    def test_ask_stream(self, ctl_ai):
        """流式: on_delta 收到增量, 回答聚合."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        lines = ["data: " + json.dumps({"choices": [
            {"delta": {"role": "assistant"}}]}),
                 "data: " + json.dumps({"choices": [
                     {"delta": {"content": "流式"}, "finish_reason": None}]}),
                 "data: " + json.dumps({"choices": [
                     {"delta": {"content": "回答"}, "finish_reason": "stop"}]}),
                 "data: [DONE]"]
        transport.add_stream(lines)
        pieces = []
        result = controller.ai_ask("请流式回答", stream=True,
                                   on_delta=pieces.append)
        assert result.ok
        assert result.detail["answer"] == "流式回答"
        assert pieces == ["流式", "回答"]

    def test_ask_auth_error_logged_failed(self, ctl_ai):
        """鉴权失败: auth 态, 留痕 status=failed."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller, api_key="sk-wrong")
        transport.add_json(status=401,
                           payload={"error": {"message": "bad key"}})
        result = controller.ai_ask("你好")
        assert not result.ok
        assert result.detail["state"] == "auth"
        rows = [row for row in controller.storage.list_operations()
                if row["action"] == "ai_ask"]
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"

    def test_ask_network_error(self, ctl_ai):
        """网络故障: network 态."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        transport.add_error(AiNetworkError("dns fail"))
        result = controller.ai_ask("你好")
        assert not result.ok
        assert result.detail["state"] == "network"


class TestAiHistory:
    """历史动作."""

    def test_history_and_clear(self, ctl_ai):
        """history 只读列出; clear 清除并留痕."""
        controller, transport, _ = ctl_ai
        enable_ai(controller)
        configure(controller)
        add_ok_reply(transport, "答一")
        add_ok_reply(transport, "答二")
        controller.ai_ask("问一")
        controller.ai_ask("问二")
        history = controller.ai_history()
        assert history.ok
        assert len(history.detail["sessions"]) == 2
        cleared = controller.ai_clear_history()
        assert cleared.ok
        assert cleared.detail["removed"] == 2
        assert controller.storage.ai_list_sessions() == []
        rows = [row for row in controller.storage.list_operations()
                if row["action"] == "ai_clear_history"]
        assert rows
