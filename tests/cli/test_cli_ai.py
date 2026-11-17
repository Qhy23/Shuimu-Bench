"""ai-* 子命令测试: 配置/问答流式输出/历史/清除(假 transport, 零联网)."""

import json

import pytest

from shuimu.cli import main
from shuimu.core.ai import AiConfigStore, MemoryAiTransport


def run(capsys, db, *argv):
    """执行 CLI 并返回 (退出码, stdout, stderr)."""
    code = main(["--db", db, *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture()
def ai_cli(tmp_path, monkeypatch):
    """AI CLI 隔离: 插件状态/外部目录/配置路径与传输层."""
    transport = MemoryAiTransport()
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(tmp_path / "ext-plugins"))
    monkeypatch.setattr("shuimu.core.controller.default_ai_config_path",
                        lambda: str(tmp_path / "ai-config.json"))
    monkeypatch.setattr("shuimu.core.controller.UrllibAiTransport",
                        lambda: transport)
    return transport


def add_ok_stream(transport):
    """注册一条流式成功响应(两段增量)."""
    transport.add_stream([
        "data: " + json.dumps({"choices": [
            {"delta": {"role": "assistant"}}]}),
        "data: " + json.dumps({"choices": [
            {"delta": {"content": "特征值是"}}]}),
        "data: " + json.dumps({"choices": [
            {"delta": {"content": "方程的根"}, "finish_reason": "stop"}]}),
        "data: [DONE]"])


def configure(capsys, tmp_path):
    """启用插件并写配置(丢弃启用命令自身的输出)."""
    assert main(["--db", str(tmp_path / "c.db"), "plugin-enable", "ai",
                 "--yes"]) == 0
    capsys.readouterr()
    AiConfigStore(str(tmp_path / "ai-config.json")).save({
        "base_url": "https://api.example.com/v1", "model": "m",
        "api_key": "k"})


class TestAiStatusConfig:
    """status 与 config."""

    def test_status_unconfigured(self, capsys, tmp_path, ai_cli):
        """未配置概况: 退出码 0, 提示未配置."""
        db = str(tmp_path / "t.db")
        code, out, _err = run(capsys, db, "ai-status")
        assert code == 0 and "未配置" in out
        assert not ai_cli.requests          # status 零联网

    def test_config_show(self, capsys, tmp_path, ai_cli):
        """--show 反映配置态."""
        db = str(tmp_path / "t.db")
        AiConfigStore(str(tmp_path / "ai-config.json")).save({
            "base_url": "https://api.example.com/v1", "model": "m"})
        code, out, _err = run(capsys, db, "ai-config", "--show")
        assert code == 0
        assert "api.example.com" in out and "未保存" in out
        assert not ai_cli.requests          # show 零联网

    def test_config_save_and_clear_key(self, capsys, tmp_path, ai_cli,
                                       monkeypatch):
        """保存配置(key 经 getpass), CLEAR 词清除."""
        db = str(tmp_path / "t.db")
        monkeypatch.setattr("getpass.getpass", lambda _p: "sk-test")
        code, out, _err = run(capsys, db, "ai-config",
                              "--base-url", "https://api.example.com/v1",
                              "--model", "m")
        assert code == 0 and "已保存" in out
        stored = AiConfigStore(str(tmp_path / "ai-config.json")).load()
        assert stored["api_key"] == "sk-test"
        monkeypatch.setattr("builtins.input", lambda _p: "")
        monkeypatch.setattr("getpass.getpass", lambda _p: "CLEAR")
        run(capsys, db, "ai-config")
        stored = AiConfigStore(str(tmp_path / "ai-config.json")).load()
        assert "api_key" not in stored
        assert stored["model"] == "m"
        assert not ai_cli.requests          # config 不联网


class TestAiAsk:
    """问答命令."""

    def test_ask_requires_enable(self, capsys, tmp_path, ai_cli):
        """未启用: 退出码 2 且零请求."""
        db = str(tmp_path / "t.db")
        code, _out, err = run(capsys, db, "ai-ask", "你好")
        assert code == 2 and "未启用" in err
        assert not ai_cli.requests

    def test_ask_stream_output(self, capsys, tmp_path, ai_cli):
        """流式: stdout 纯回答, stderr 上下文统计."""
        db = str(tmp_path / "t.db")
        configure(capsys, tmp_path)
        add_ok_stream(ai_cli)
        code, out, err = run(capsys, db, "ai-ask", "什么是特征值")
        assert code == 0
        assert out.rstrip("\n") == "特征值是方程的根"
        assert "上下文" in err

    def test_ask_no_stream(self, capsys, tmp_path, ai_cli):
        """--no-stream: 一次性输出."""
        db = str(tmp_path / "t.db")
        configure(capsys, tmp_path)
        transport = ai_cli
        transport.add_json(payload={"choices": [{"message": {
            "role": "assistant", "content": "整段回答"},
            "finish_reason": "stop"}], "model": "m"})
        code, out, _err = run(capsys, db, "ai-ask", "问", "--no-stream")
        assert code == 0 and "整段回答" in out

    def test_ask_auth_error(self, capsys, tmp_path, ai_cli):
        """鉴权失败: 退出码 2, stderr 有提示."""
        db = str(tmp_path / "t.db")
        configure(capsys, tmp_path)
        ai_cli.add_stream(['{"error": {"message": "bad key"}}'],
                          status=401)
        code, _out, err = run(capsys, db, "ai-ask", "问")
        assert code == 2 and "鉴权失败" in err


class TestAiHistoryClear:
    """历史与清除."""

    def _asked(self, capsys, tmp_path, ai_cli):
        """公共: 启用 + 配置 + 一次问答."""
        db = str(tmp_path / "t.db")
        configure(capsys, tmp_path)
        add_ok_stream(ai_cli)
        assert run(capsys, db, "ai-ask", "第一问")[0] == 0
        return db

    def test_history_empty(self, capsys, tmp_path, ai_cli):
        """空历史提示."""
        db = str(tmp_path / "t.db")
        code, out, _err = run(capsys, db, "ai-history")
        assert code == 0 and "还没有对话" in out
        assert not ai_cli.requests          # history 零联网

    def test_history_list_and_session(self, capsys, tmp_path, ai_cli):
        """列表与会话展开."""
        db = self._asked(capsys, tmp_path, ai_cli)
        code, out, _err = run(capsys, db, "ai-history")
        assert code == 0 and "第一问" in out
        code, out, _err = run(capsys, db, "ai-history", "--session", "1")
        assert code == 0
        assert "我: 第一问" in out and "AI: 特征值是方程的根" in out
        code, _out, err = run(capsys, db, "ai-history", "--session", "99")
        assert code == 2 and "不存在" in err

    def test_clear_requires_confirm(self, capsys, tmp_path, ai_cli,
                                    monkeypatch):
        """无 --yes: 输入 n 取消."""
        db = self._asked(capsys, tmp_path, ai_cli)
        monkeypatch.setattr("builtins.input", lambda _p: "n")
        code, out, _err = run(capsys, db, "ai-clear")
        assert code == 0 and "已取消" in out
        code, out, _err = run(capsys, db, "ai-history")
        assert "第一问" in out
        assert ai_cli.requests              # 仅此前一次问答联网

    def test_clear_yes(self, capsys, tmp_path, ai_cli):
        """--yes 直接清除."""
        db = self._asked(capsys, tmp_path, ai_cli)
        code, out, _err = run(capsys, db, "ai-clear", "--yes")
        assert code == 0 and "已清除" in out
        code, out, _err = run(capsys, db, "ai-history")
        assert "还没有对话" in out
