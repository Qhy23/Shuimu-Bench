"""ai 插件测试: run(job) 动作分派与 run_plugin 通道(假 transport, 零联网)."""

import json
import os
import shutil

import pytest

import shuimu.plugins.ai.plugin as ai_plugin
from shuimu.core.ai import AiConfigStore, MemoryAiTransport
from shuimu.core.pluginapi import PluginJob
from shuimu.core.pluginman import PluginManager
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage


def _ok_reply(transport, content="回答内容"):
    """注册一条成功响应."""
    transport.add_json(payload={"choices": [{"message": {
        "role": "assistant", "content": content},
        "finish_reason": "stop"}], "model": "m"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """插件工厂注入 tmp; 返回 (transport, db_path, config_path)."""
    transport = MemoryAiTransport()
    db_path = str(tmp_path / "plug.db")
    config_path = str(tmp_path / "ai-config.json")
    monkeypatch.setattr(ai_plugin, "_transport_factory", lambda: transport)
    monkeypatch.setattr(ai_plugin, "_storage", lambda: Storage(db_path))
    monkeypatch.setattr(ai_plugin, "_config_store",
                        lambda: AiConfigStore(config_path))
    return transport, db_path, config_path


def _job(params):
    return PluginJob(targets=[], params=params)


class TestManifest:
    """清单发现与门禁."""

    def test_discovered_and_network_disabled_by_default(self, tmp_path):
        """内置清单可被发现; network 默认禁用."""
        manager = PluginManager(external_dir=str(tmp_path / "ext"),
                                state_path=str(tmp_path / "plugins.yaml"))
        record = manager.get("ai")
        assert record is not None
        assert record.manifest is not None
        assert record.manifest.name == "AI 工具"
        assert record.status == "disabled"
        assert "network" in record.manifest.permissions

    def test_enable_authorizes(self, tmp_path):
        """启用后状态 ok(=联网授权)."""
        manager = PluginManager(external_dir=str(tmp_path / "ext"),
                                state_path=str(tmp_path / "plugins.yaml"))
        manager.set_enabled("ai", True)
        assert manager.get("ai").status == "ok"


class TestStatus:
    """status 动作."""

    def test_unconfigured_warn(self, env):
        """未配置: warn finding(ok 只看 error 级, 仍为 True)."""
        _transport, _db, _cfg = env
        report = ai_plugin.run(_job({"action": "status"}))
        assert report.ok
        assert any(f.severity == "warn" and f.check == "config"
                   for f in report.findings)

    def test_configured_ok(self, env):
        """已配置: ok finding 反映三项配置."""
        _transport, _db, config_path = env
        AiConfigStore(config_path).save({
            "base_url": "https://api.example.com/v1",
            "model": "m", "api_key": "k"})
        report = ai_plugin.run(_job({"action": "status"}))
        assert report.ok
        assert all(f.severity != "warn" for f in report.findings)


class TestAsk:
    """ask 动作."""

    def test_missing_question(self, env):    # pylint: disable=unused-argument
        """缺问题: error finding."""
        report = ai_plugin.run(_job({"action": "ask"}))
        assert not report.ok

    def test_unconfigured(self, env):        # pylint: disable=unused-argument
        """未配置: error finding."""
        report = ai_plugin.run(_job({"action": "ask", "question": "q"}))
        assert not report.ok
        assert report.findings[0].check == "config"

    def test_ask_success_round_logged(self, env):
        """成功: 回答入 finding, 历史落库."""
        transport, db_path, config_path = env
        AiConfigStore(config_path).save({
            "base_url": "https://api.example.com/v1", "model": "m"})
        _ok_reply(transport, "特征值是...")
        report = ai_plugin.run(_job({"action": "ask",
                                     "question": "什么是特征值"}))
        assert report.ok
        assert report.findings[0].title.startswith("特征值是")
        storage = Storage(db_path)
        try:
            sessions = storage.ai_list_sessions()
            assert len(sessions) == 1
            assert len(storage.ai_list_messages(sessions[0]["id"])) == 2
        finally:
            storage.close()

    def test_ask_once_key_overrides(self, env):
        """secret 参数一次性优先: 不写盘, 请求头用传入 key."""
        transport, _db, config_path = env
        AiConfigStore(config_path).save({
            "base_url": "https://api.example.com/v1", "model": "m",
            "api_key": "sk-saved"})
        _ok_reply(transport)
        ai_plugin.run(_job({"action": "ask", "question": "q",
                            "api_key": "sk-once"}))
        assert transport.requests[0][1]["Authorization"] == "Bearer sk-once"
        stored = AiConfigStore(config_path).load()
        assert stored["api_key"] == "sk-saved"

    def test_long_answer_truncated_in_finding(self, env):
        """超长回答 finding 截断到 400 字."""
        transport, _db, config_path = env
        AiConfigStore(config_path).save({
            "base_url": "https://api.example.com/v1", "model": "m"})
        _ok_reply(transport, "字" * 500)
        report = ai_plugin.run(_job({"action": "ask", "question": "q"}))
        answer = next(f for f in report.findings if f.check == "answer")
        assert len(answer.title) == 400
        assert "500 字" in answer.detail

    def test_auth_error(self, env):
        """鉴权失败: error finding, check 标识带 kind."""
        transport, _db, config_path = env
        AiConfigStore(config_path).save({
            "base_url": "https://api.example.com/v1", "model": "m"})
        transport.add_json(status=401,
                           payload={"error": {"message": "bad key"}})
        report = ai_plugin.run(_job({"action": "ask", "question": "q"}))
        assert not report.ok
        assert report.findings[0].check == "ai_auth"


class TestHistoryAndClear:
    """history/clear 动作."""

    def test_history_empty(self, env):       # pylint: disable=unused-argument
        """空历史提示."""
        report = ai_plugin.run(_job({"action": "history"}))
        assert report.ok

    def test_history_and_clear(self, env):
        """问答后 history 列出, clear 清空."""
        transport, db_path, config_path = env
        AiConfigStore(config_path).save({
            "base_url": "https://api.example.com/v1", "model": "m"})
        _ok_reply(transport)
        ai_plugin.run(_job({"action": "ask", "question": "第一问"}))
        history = ai_plugin.run(_job({"action": "history"}))
        assert any("第一问" in f.title for f in history.findings)
        cleared = ai_plugin.run(_job({"action": "clear"}))
        assert cleared.ok
        storage = Storage(db_path)
        try:
            assert storage.ai_list_sessions() == []
        finally:
            storage.close()


class TestRunPluginChannel:
    """经 controller.run_plugin 的通用通道.

    pluginman 从文件路径独立加载入口模块(不入 sys.modules), 包内
    monkeypatch 不影响它; 因此用真 manifest + 记录参数的替身入口,
    builtin 目录整体指向替身目录来测 run_plugin 的 secret 剔除纪律.
    """

    def test_secret_param_not_logged(self, tmp_path, monkeypatch):
        """secret 参数值收到但只记参数名, 不入留痕."""
        fake_builtin = tmp_path / "fake-builtin" / "ai"
        fake_builtin.mkdir(parents=True)
        shutil.copy(os.path.join(os.path.dirname(ai_plugin.__file__),
                                 "manifest.yaml"),
                    str(fake_builtin / "manifest.yaml"))
        received = tmp_path / "received.json"
        (fake_builtin / "plugin.py").write_text(
            "import json\n"
            "from shuimu.core.pluginapi import finding, make_report\n"
            "\n"
            "\n"
            "def run(job):\n"
            f"    with open(r'{received}', 'w', encoding='utf-8') as f:\n"
            "        json.dump(job.params, f, ensure_ascii=False)\n"
            "    return make_report([finding('ok', 'ask', '已回答')],\n"
            "                       summary='已回答')\n",
            encoding="utf-8")
        monkeypatch.setattr(
            "shuimu.core.pluginman.builtin_plugins_dir",
            lambda: str(tmp_path / "fake-builtin"))
        monkeypatch.setattr(
            "shuimu.core.controller.template_links_path",
            lambda: str(tmp_path / "no.yaml"))
        storage = Storage(str(tmp_path / "run.db"))
        controller = Controller(
            storage, schedule_path=str(tmp_path / "s.yaml"),
            links_path=str(tmp_path / "l.yaml"),
            plugins_state_path=str(tmp_path / "plugins.yaml"),
            external_plugins_dir=str(tmp_path / "ext-empty"),
            ai_config_path=str(tmp_path / "ai.json"))
        controller.plugins.set_enabled("ai", True)
        result = controller.run_plugin(
            "ai", [], {"action": "ask", "question": "你好",
                       "api_key": "sk-secret-value"})
        assert result.ok, result.message
        # 插件确实收到了 secret 值
        assert json.loads(received.read_text(encoding="utf-8"))[
            "api_key"] == "sk-secret-value"
        # 留痕里只有参数名, 没有值
        rows = [row for row in storage.list_operations()
                if row["action"] == "plugin:ai"]
        assert rows
        logged = rows[0]["params"]
        assert "sk-secret-value" not in logged
        assert json.loads(logged).get("secret_params") == ["api_key"]
        assert json.loads(logged).get("api_key") is None
        storage.close()
