"""ai.py 单元测试: SSE 解析, 客户端(非流式/流式/错误分类), 配置存储."""

import json
import os
import stat

import pytest

from shuimu.core.ai import (
    AiClient,
    AiConfig,
    AiConfigStore,
    AiError,
    AiNetworkError,
    MemoryAiTransport,
    iter_sse_data,
    parse_stream_delta,
)

CONFIG = AiConfig(base_url="https://api.example.com/v1",
                  api_key="sk-test", model="test-model")
MESSAGES = [{"role": "user", "content": "你好"}]


def _client(transport):
    return AiClient(CONFIG, transport)


def _chat_payload(content="你好!", finish="stop", model="test-model"):
    """构造一条非流式 chat/completions 响应体."""
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                      "total_tokens": 5},
            "model": model}


def _delta_json(payload, finish=None):
    """构造一条流式增量的裸 JSON 载荷(不带 data: 前缀)."""
    return json.dumps({"choices": [{"delta": payload,
                                    "finish_reason": finish}]},
                      ensure_ascii=False)


def _delta_line(payload, finish=None):
    """构造一条流式增量 SSE 行(带 data: 前缀)."""
    return "data: " + _delta_json(payload, finish)


class TestIterSseData:
    """SSE 行过滤."""

    def test_data_with_and_without_space(self):
        """冒号后有无空格都能取到载荷."""
        lines = ["data: a", "data:b", "data: c\n"]
        assert list(iter_sse_data(lines)) == ["a", "b", "c"]

    def test_crlf_stripped(self):
        """CRLF 行尾剥掉后不影响载荷."""
        assert list(iter_sse_data(["data: x\r\n"])) == ["x"]

    def test_comment_and_other_fields_skipped(self):
        """keep-alive 注释行与 event/id/retry 行跳过."""
        lines = [": keep-alive", "event: message", "id: 1", "retry: 100",
                 "data: ok"]
        assert list(iter_sse_data(lines)) == ["ok"]

    def test_done_terminates(self):
        """[DONE] 终止迭代且自身不产出."""
        lines = ["data: first", "data: [DONE]", "data: after"]
        assert list(iter_sse_data(lines)) == ["first"]

    def test_plain_line_skipped(self):
        """非 data: 行跳过."""
        assert not list(iter_sse_data(["hello", ""]))


class TestParseStreamDelta:
    """单载荷解析."""

    def test_content_delta(self):
        """正常增量文本取出."""
        assert parse_stream_delta(_delta_json({"content": "水木"})) == "水木"

    def test_role_first_chunk_empty(self):
        """role 首块无 content 字段返回空串."""
        assert parse_stream_delta(_delta_json({"role": "assistant"})) == ""

    def test_bad_json_raises_protocol(self):
        """非合法 JSON 抛 protocol 错."""
        with pytest.raises(AiError) as exc:
            parse_stream_delta("{bad")
        assert exc.value.kind == "protocol"

    def test_non_object_raises_protocol(self):
        """非 JSON 对象抛 protocol 错."""
        with pytest.raises(AiError) as exc:
            parse_stream_delta("[1, 2]")
        assert exc.value.kind == "protocol"


class TestAiClientNonStream:
    """非流式对话."""

    def test_success(self):
        """正常回复取 content/usage/finish/model."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_chat_payload("答案"))
        reply = _client(transport).chat(MESSAGES)
        assert reply.content == "答案"
        assert reply.finish_reason == "stop"
        assert reply.usage["total_tokens"] == 5
        assert reply.model == "test-model"

    def test_auth_header(self):
        """有 key 时带 Bearer 头, 请求体含 model 与 messages."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_chat_payload())
        _client(transport).chat(MESSAGES)
        url, headers, body, _stream = transport.requests[0]
        assert url == "https://api.example.com/v1/chat/completions"
        assert headers["Authorization"] == "Bearer sk-test"
        assert body["model"] == "test-model"
        assert body["messages"] == MESSAGES
        assert body["stream"] is False

    def test_no_auth_header_without_key(self):
        """key 为空时不发 Authorization(Ollama 免鉴权)."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_chat_payload())
        client = AiClient(AiConfig("https://api.example.com/v1", "",
                                   "m"), transport)
        client.chat(MESSAGES)
        assert "Authorization" not in transport.requests[0][1]

    def test_trailing_slash_base_url(self):
        """base_url 尾斜杠不影响端点拼接."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_chat_payload())
        client = AiClient(AiConfig("https://api.example.com/v1/", "k",
                                   "m"), transport)
        client.chat(MESSAGES)
        assert transport.requests[0][0] == (
            "https://api.example.com/v1/chat/completions")

    def test_auth_error_with_message(self):
        """401 归 auth, 消息取 error.message."""
        transport = MemoryAiTransport()
        transport.add_json(status=401,
                           payload={"error": {"message": "invalid key"}})
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "auth"
        assert exc.value.status == 401
        assert "invalid key" in str(exc.value)

    def test_forbidden_is_auth(self):
        """403 同归 auth."""
        transport = MemoryAiTransport()
        transport.add_json(status=403, text="forbidden")
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "auth"

    def test_rate_limit(self):
        """429 归 rate_limit."""
        transport = MemoryAiTransport()
        transport.add_json(status=429, payload={"message": "slow down"})
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "rate_limit"
        assert "slow down" in str(exc.value)

    def test_server_error(self):
        """5xx 归 server."""
        transport = MemoryAiTransport()
        transport.add_json(status=502, text="bad gateway")
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "server"

    def test_unexpected_status(self):
        """其余非 2xx 归 protocol."""
        transport = MemoryAiTransport()
        transport.add_json(status=302, text="")
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "protocol"

    def test_bad_json_body(self):
        """响应体非 JSON 归 protocol."""
        transport = MemoryAiTransport()
        transport.add_json(text="<html>")
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "protocol"

    def test_missing_choices(self):
        """缺 choices 字段归 protocol."""
        transport = MemoryAiTransport()
        transport.add_json(payload={"model": "m"})
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "protocol"

    def test_network_error(self):
        """网络层故障归 network."""
        transport = MemoryAiTransport()
        transport.add_error(AiNetworkError("dns fail"))
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES)
        assert exc.value.kind == "network"
        assert "dns fail" in str(exc.value)

    def test_unconfigured(self):
        """base_url/model 未配置直接抛 config, 不发请求."""
        transport = MemoryAiTransport()
        client = AiClient(AiConfig("", "", ""), transport)
        with pytest.raises(AiError) as exc:
            client.chat(MESSAGES)
        assert exc.value.kind == "config"
        assert not transport.requests


class TestAiClientStream:
    """流式对话."""

    def test_stream_aggregates_and_calls_on_delta(self):
        """增量序列聚合为全文, on_delta 逐块收到."""
        transport = MemoryAiTransport()
        lines = ["data: " + json.dumps(
                     {"choices": [{"delta": {"role": "assistant"}}]}),
                 _delta_line({"content": "水木"}),
                 _delta_line({"content": "工作"}),
                 _delta_line({"content": "台"}, finish="stop"),
                 "data: [DONE]"]
        transport.add_stream(lines)
        pieces = []
        reply = _client(transport).chat(MESSAGES, stream=True,
                                        on_delta=pieces.append)
        assert reply.content == "水木工作台"
        assert pieces == ["水木", "工作", "台"]
        assert reply.finish_reason == "stop"
        assert transport.requests[0][3] is True

    def test_stream_request_body(self):
        """流式请求体 stream=true."""
        transport = MemoryAiTransport()
        transport.add_stream(["data: [DONE]"])
        _client(transport).chat(MESSAGES, stream=True)
        assert transport.requests[0][2]["stream"] is True

    def test_stream_usage_chunk(self):
        """末尾 usage 块(部分服务附带)被提取."""
        transport = MemoryAiTransport()
        lines = [_delta_line({"content": "hi"}),
                 "data: " + json.dumps({"choices": [],
                                        "usage": {"total_tokens": 9}}),
                 "data: [DONE]"]
        transport.add_stream(lines)
        reply = _client(transport).chat(MESSAGES, stream=True)
        assert reply.content == "hi"
        assert reply.usage == {"total_tokens": 9}

    def test_stream_keepalive_ignored(self):
        """注释行与空行不产出内容."""
        transport = MemoryAiTransport()
        transport.add_stream([": keep-alive", _delta_line({"content": "x"}),
                              "data: [DONE]"])
        reply = _client(transport).chat(MESSAGES, stream=True)
        assert reply.content == "x"

    def test_stream_bad_payload(self):
        """流中坏 JSON 抛 protocol."""
        transport = MemoryAiTransport()
        transport.add_stream(["data: {bad"])
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES, stream=True)
        assert exc.value.kind == "protocol"

    def test_stream_http_error(self):
        """流式请求 4xx 同样分类(错误体整读)."""
        transport = MemoryAiTransport()
        transport.add_stream(['{"error": {"message": "bad key"}}'],
                             status=401)
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES, stream=True)
        assert exc.value.kind == "auth"
        assert "bad key" in str(exc.value)

    def test_stream_network_error(self):
        """流式网络层故障归 network."""
        transport = MemoryAiTransport()
        transport.add_error(AiNetworkError("timeout"))
        with pytest.raises(AiError) as exc:
            _client(transport).chat(MESSAGES, stream=True)
        assert exc.value.kind == "network"


class TestAiConfigStore:
    """配置存储."""

    def test_save_load_roundtrip(self, tmp_path):
        """写入后读回三键."""
        store = AiConfigStore(str(tmp_path / "ai-config.json"))
        store.save({"base_url": "https://api.example.com/v1",
                    "model": "m", "api_key": "k"})
        assert store.load() == {"base_url": "https://api.example.com/v1",
                                "model": "m", "api_key": "k"}

    def test_file_mode_600(self, tmp_path):
        """文件权限 0o600."""
        path = str(tmp_path / "ai-config.json")
        AiConfigStore(path).save({"api_key": "k"})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_missing_returns_empty(self, tmp_path):
        """文件缺失返回空 dict."""
        store = AiConfigStore(str(tmp_path / "none.json"))
        assert store.load() == {}

    def test_corrupt_returns_empty(self, tmp_path):
        """损坏 JSON 返回空 dict 不抛."""
        path = tmp_path / "ai-config.json"
        path.write_text("{bad", encoding="utf-8")
        assert AiConfigStore(str(path)).load() == {}

    def test_merge_save(self, tmp_path):
        """部分键写入只覆盖该键."""
        store = AiConfigStore(str(tmp_path / "ai-config.json"))
        store.save({"base_url": "u", "model": "m"})
        store.save({"model": "m2"})
        assert store.load() == {"base_url": "u", "model": "m2"}

    def test_clear_key_keeps_others(self, tmp_path):
        """clear_key 只删 api_key, base_url/model 保留."""
        store = AiConfigStore(str(tmp_path / "ai-config.json"))
        store.save({"base_url": "u", "model": "m", "api_key": "k"})
        store.clear_key()
        assert store.load() == {"base_url": "u", "model": "m"}

    def test_unknown_keys_ignored(self, tmp_path):
        """未知键与非字符串值被忽略."""
        path = tmp_path / "ai-config.json"
        path.write_text(json.dumps({"base_url": "u", "extra": 1,
                                    "model": 5}), encoding="utf-8")
        assert AiConfigStore(str(path)).load() == {"base_url": "u"}


ANTHROPIC_BASE = "https://open.bigmodel.cn/api/anthropic"


def _anthropic_config():
    """Anthropic 协议配置(base_url 含 /anthropic 片段)."""
    return AiConfig(base_url=ANTHROPIC_BASE, api_key="sk-ant",
                    model="glm-5.3")


def _anthropic_payload(text="答案", stop="end_turn"):
    """构造一条 Anthropic 非流式响应体."""
    return {"type": "message", "role": "assistant", "model": "glm-5.3",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
            "usage": {"input_tokens": 10, "output_tokens": 3}}


def _anth_event(obj):
    """构造一条 Anthropic SSE 行(event 行 + data 行)."""
    return ["event: " + obj.get("type", "x"),
            "data: " + json.dumps(obj, ensure_ascii=False)]


class TestAnthropicNonStream:
    """Anthropic 协议非流式."""

    def test_protocol_inferred_and_headers(self):
        """base_url 含 /anthropic: 端点 /v1/messages, x-api-key 头."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_anthropic_payload())
        AiClient(_anthropic_config(), transport).chat(MESSAGES)
        url, headers, body, _stream = transport.requests[0]
        assert url == (ANTHROPIC_BASE + "/v1/messages")
        assert headers["x-api-key"] == "sk-ant"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers
        assert body["max_tokens"] == 4096

    def test_success_and_usage_mapped(self):
        """content 数组拼接, usage 映射为 OpenAI 键名."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_anthropic_payload("第一段"))
        reply = AiClient(_anthropic_config(), transport).chat(MESSAGES)
        assert reply.content == "第一段"
        assert reply.finish_reason == "end_turn"
        assert reply.usage == {"prompt_tokens": 10,
                               "completion_tokens": 3, "total_tokens": 13}
        assert reply.model == "glm-5.3"

    def test_system_lifted_to_top_level(self):
        """system 消息抽为顶层 system 参数, 不进 messages."""
        transport = MemoryAiTransport()
        transport.add_json(payload=_anthropic_payload())
        messages = [{"role": "system", "content": "资料"}, MESSAGES[0]]
        AiClient(_anthropic_config(), transport).chat(messages)
        body = transport.requests[0][2]
        assert body["system"] == "资料"
        assert body["messages"] == [MESSAGES[0]]

    def test_error_body_message_extracted(self):
        """Anthropic 错误体 error.message 提取, 401 归 auth."""
        transport = MemoryAiTransport()
        transport.add_json(status=401, payload={
            "type": "error",
            "error": {"type": "authentication_error",
                      "message": "invalid api key"}})
        with pytest.raises(AiError) as exc:
            AiClient(_anthropic_config(), transport).chat(MESSAGES)
        assert exc.value.kind == "auth"
        assert "invalid api key" in str(exc.value)

    def test_missing_content_protocol(self):
        """缺 content 字段归 protocol."""
        transport = MemoryAiTransport()
        transport.add_json(payload={"type": "message"})
        with pytest.raises(AiError) as exc:
            AiClient(_anthropic_config(), transport).chat(MESSAGES)
        assert exc.value.kind == "protocol"

    def test_balance_error_is_rate_limit(self):
        """智谱余额不足形态(429 + error.code 1113)归 rate_limit."""
        transport = MemoryAiTransport()
        transport.add_json(status=429, payload={
            "type": "error",
            "error": {"code": "1113",
                      "message": "余额不足或无可用资源包,请充值。"}})
        with pytest.raises(AiError) as exc:
            AiClient(_anthropic_config(), transport).chat(MESSAGES)
        assert exc.value.kind == "rate_limit"


class TestAnthropicStream:
    """Anthropic 协议流式."""

    def test_stream_events_aggregate(self):
        """事件流逐块回调, 增量聚合, usage/stop_reason 提取."""
        transport = MemoryAiTransport()
        lines = []
        lines += _anth_event({"type": "message_start", "message": {
            "model": "glm-5.3", "usage": {"input_tokens": 7}}})
        lines += _anth_event({"type": "ping"})
        lines += _anth_event({"type": "content_block_start",
                              "index": 0})
        lines += _anth_event({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "text_delta",
                                        "text": "水木"}})
        lines += _anth_event({"type": "content_block_delta", "index": 0,
                              "delta": {"type": "text_delta",
                                        "text": "工作台"}})
        lines += _anth_event({"type": "content_block_stop", "index": 0})
        lines += _anth_event({"type": "message_delta",
                              "delta": {"stop_reason": "end_turn"},
                              "usage": {"output_tokens": 4}})
        lines += _anth_event({"type": "message_stop"})
        lines.append("data: [DONE]")
        transport.add_stream(lines)
        pieces = []
        reply = AiClient(_anthropic_config(), transport).chat(
            MESSAGES, stream=True, on_delta=pieces.append)
        assert reply.content == "水木工作台"
        assert pieces == ["水木", "工作台"]
        assert reply.finish_reason == "end_turn"
        assert reply.usage == {"prompt_tokens": 7, "completion_tokens": 4,
                               "total_tokens": 11}
        assert reply.model == "glm-5.3"

    def test_stream_non_text_delta_ignored(self):
        """非 text_delta 增量(如工具调用)不产出文本."""
        transport = MemoryAiTransport()
        lines = _anth_event({"type": "content_block_delta",
                             "delta": {"type": "input_json_delta",
                                       "partial_json": "{\"x\""}})
        lines += _anth_event({"type": "content_block_delta",
                              "delta": {"type": "text_delta",
                                        "text": "ok"}})
        lines.append("data: [DONE]")
        transport.add_stream(list(lines))
        reply = AiClient(_anthropic_config(), transport).chat(
            MESSAGES, stream=True)
        assert reply.content == "ok"

    def test_stream_bad_json(self):
        """流中坏 JSON 抛 protocol."""
        transport = MemoryAiTransport()
        transport.add_stream(["data: {bad"])
        with pytest.raises(AiError) as exc:
            AiClient(_anthropic_config(), transport).chat(
                MESSAGES, stream=True)
        assert exc.value.kind == "protocol"
