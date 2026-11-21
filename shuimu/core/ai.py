"""OpenAI 兼容 + Anthropic 协议聊天客户端: 最小 transport, urllib, SSE 流式.

不引第三方 SDK; 按服务分两种协议, 由 base_url 自动推断:

- OpenAI 兼容(智谱 paas/v4 / DeepSeek / Kimi / Ollama 等): base_url 为
  API 根(如 https://api.deepseek.com/v1), 拼 /chat/completions,
  api_key 经 Bearer 头携带, 为空时不发 Authorization(本地 Ollama 免鉴权).
- Anthropic Messages(base_url 含 "/anthropic", 如智谱给 Claude Code
  用户的 https://open.bigmodel.cn/api/anthropic): 拼 /v1/messages,
  x-api-key + anthropic-version 头, system 消息抽为顶层 system 参数,
  max_tokens 必填(内定 4096); 该端点的 key 资源包与 paas/v4 不通用.

传输层自带最小协议(post_json 整读 + post_stream 逐行), 与 campus_net 的
"单跳整读"契约互不影响; 非流式响应复用 campus_net.HttpResponse 纯数据类.
SSE 解析为行迭代器纯函数, 不做多行 data 聚合(两种协议均为单行载荷;
如需支持多行 data 的服务再扩展聚合模式).

api_key 永不入留痕; 用户级配置(base_url/model/api_key)由 AiConfigStore
落 data/ai-config.json(0o600), 不入 config.yaml(会随仓库分发).
"""

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

from shuimu.core.campus_net import HttpResponse

AI_USER_AGENT = "ShuimuBench/0.1"
CONFIG_KEYS = ("base_url", "model", "api_key")
ERROR_KINDS = ("config", "auth", "rate_limit", "server", "network", "protocol")
ANTHROPIC_VERSION = "2023-06-01"      # Anthropic Messages 协议版本头
ANTHROPIC_MAX_TOKENS = 4096           # Anthropic 协议 max_tokens 必填
THINKING_BUDGET = 2000                 # 深度思考的思考 token 预算
ANTHROPIC_THINKING_MAX_TOKENS = 8192   # thinking 时 max_tokens 须大于预算
ANTHROPIC_MARK = "/anthropic"         # base_url 含此片段则走 Anthropic 协议


class AiError(Exception):
    """AI 调用失败; kind 取 ERROR_KINDS 之一.

    config: base_url/model 未配置; auth: 401/403(key 错误或无权限);
    rate_limit: 429; server: 5xx; network: DNS/超时/TLS 等网络层故障;
    protocol: 响应非合法 JSON 或缺少约定字段.
    """

    def __init__(self, kind: str, message: str, status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status


class AiNetworkError(OSError):
    """网络层故障(DNS/超时/TLS 等); 由 AiClient 统一转 kind='network'."""


@dataclass(frozen=True)
class AiConfig:
    """一次调用的全部配置; timeout 对连接与流式读同样生效."""

    base_url: str
    api_key: str
    model: str
    timeout: int = 120


@dataclass
class AiReply:
    """一轮对话的结果; thinking 为思考文本(未请求或服务端未给则为空).

    流式时 usage 可能为空 dict(服务端未回传).
    """

    content: str
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    model: str = ""
    thinking: str = ""


@dataclass
class StreamResponse:
    """流式响应骨架: 状态与头先到手, lines 为已解码文本行迭代器(含行尾)."""

    status: int
    headers: list = field(default_factory=list)
    url: str = ""
    lines: object = None


class AiTransport:
    """AI 客户端最小传输协议: JSON POST 整读 + 流式 POST 逐行."""

    def post_json(self, url: str, headers: dict, body: dict) -> HttpResponse:
        """发起 JSON POST 并整读响应; 由子类实现."""
        raise NotImplementedError

    def post_stream(self, url: str, headers: dict, body: dict) -> StreamResponse:
        """发起 JSON POST 并返回流式响应; 消费方逐行读 lines.

        网络层故障(DNS/超时/TLS)抛 AiNetworkError; 4xx/5xx 不抛,
        原样返回(错误体供上层分类).
        """
        raise NotImplementedError


class UrllibAiTransport(AiTransport):
    """生产实现: urllib.request, 尊重代理环境变量; 超时由构造给定."""

    def __init__(self, timeout: int = 120):
        self._timeout = timeout

    def post_json(self, url: str, headers: dict, body: dict) -> HttpResponse:
        request = self._request(url, headers, body)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                raw = resp.read()
                return HttpResponse(resp.status, list(resp.headers.items()),
                                    raw.decode("utf-8", errors="replace"),
                                    url, raw)
        except urllib.error.HTTPError as exc:
            return self._error_response(exc, url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AiNetworkError(str(exc)) from exc

    def post_stream(self, url: str, headers: dict, body: dict) -> StreamResponse:
        request = self._request(url, headers, body)
        try:
            # resp 交由 _decoded_lines 生成器内的 with 随迭代关闭
            resp = urllib.request.urlopen(  # pylint: disable=consider-using-with
                request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            return self._error_stream(exc, url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AiNetworkError(str(exc)) from exc
        return StreamResponse(status=resp.status,
                              headers=list(resp.headers.items()), url=url,
                              lines=self._decoded_lines(resp))

    @staticmethod
    def _request(url: str, headers: dict, body: dict) -> urllib.request.Request:
        request_headers = {"User-Agent": AI_USER_AGENT,
                           "Content-Type": "application/json"}
        request_headers.update(headers or {})
        return urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers=request_headers, method="POST")

    @staticmethod
    def _decoded_lines(resp) -> Iterator[str]:
        """逐行产出已解码文本; 生成器保证响应对象随迭代关闭."""
        with resp:
            for line in resp:
                yield line.decode("utf-8", errors="replace")

    @staticmethod
    def _error_response(exc: urllib.error.HTTPError, url: str) -> HttpResponse:
        """4xx/5xx: 读出错误体原样返回, 由上层分类."""
        try:
            raw = exc.read()
            return HttpResponse(exc.code, list(exc.headers.items()),
                                raw.decode("utf-8", errors="replace"),
                                url, raw)
        finally:
            exc.close()

    @classmethod
    def _error_stream(cls, exc: urllib.error.HTTPError,
                      url: str) -> StreamResponse:
        """流式请求的 4xx/5xx: 错误体整读为单行迭代器."""
        response = cls._error_response(exc, url)
        return StreamResponse(status=response.status,
                              headers=response.headers, url=url,
                              lines=iter(response.text.splitlines(keepends=True)))


class MemoryAiTransport(AiTransport):
    """测试实现: 按注册顺序消费响应队列, 记录全部请求(含 headers)供断言."""

    def __init__(self):
        self._responses = []      # [(kind, payload)]; kind: json|stream|error
        self.requests = []        # [(url, headers, body, stream)]

    def add_json(self, status: int = 200, payload: object = None,
                 text: str = "") -> None:
        """注册非流式响应; payload 给定时序列化为响应体(text 忽略)."""
        if payload is not None:
            text = json.dumps(payload, ensure_ascii=False)
        self._responses.append(
            ("json", HttpResponse(status=status, text=text, url="")))

    def add_stream(self, lines: list, status: int = 200) -> None:
        """注册流式响应; lines 为原始 SSE 行(含 data: 前缀/[DONE]/注释行)."""
        self._responses.append(("stream", (status, list(lines))))

    def add_error(self, exc: Exception) -> None:
        """注册网络层故障(请求时抛出)."""
        self._responses.append(("error", exc))

    def _consume(self, url: str, headers: dict, body: dict, stream: bool):
        self.requests.append((url, dict(headers or {}), dict(body), stream))
        if not self._responses:
            raise AssertionError("MemoryAiTransport 无注册响应")
        kind, payload = self._responses.pop(0)
        if kind == "error":
            raise payload
        if kind == "json":
            if stream:
                raise AssertionError("注册的是非流式响应, 但收到流式请求")
            return payload
        status, lines = payload
        if not stream:
            raise AssertionError("注册的是流式响应, 但收到非流式请求")
        return StreamResponse(status=status, headers=[], url=url,
                              lines=iter(lines))

    def post_json(self, url: str, headers: dict, body: dict) -> HttpResponse:
        return self._consume(url, headers, body, False)

    def post_stream(self, url: str, headers: dict, body: dict) -> StreamResponse:
        return self._consume(url, headers, body, True)


def iter_sse_data(lines) -> Iterator[str]:
    """行迭代器 -> data: 载荷迭代器.

    行 rstrip("\\r\\n") 后以 "data:" 开头才处理, 冒号后可无空格;
    ":" 开头注释行(keep-alive)与 event/id/retry 字段行跳过;
    载荷 "[DONE]" 终止迭代(不产出).
    """
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.startswith("data:"):
            continue
        payload = line[5:]
        if payload.startswith(" "):
            payload = payload[1:]
        if payload == "[DONE]":
            return
        yield payload


def parse_stream_delta(payload: str) -> str:
    """单个 data: 载荷 -> choices[0].delta.content 增量文本.

    JSON 非法抛 AiError('protocol'); 字段缺失(如 role 首块)返回空串.
    """
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise AiError("protocol",
                      f"SSE 载荷不是合法 JSON: {payload[:80]}") from exc
    if not isinstance(data, dict):
        raise AiError("protocol", "SSE 载荷不是 JSON 对象")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0] if isinstance(choices[0], dict) else {}
    delta = choice.get("delta")
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) else ""


def parse_anthropic_delta(data: dict) -> str:
    """Anthropic SSE 载荷(已解析的 dict) -> text_delta 增量文本.

    载荷自带 type 字段(event: 行无需依赖); 非 content_block_delta 或
    非文本增量(如工具调用)返回空串.
    """
    if data.get("type") != "content_block_delta":
        return ""
    delta = data.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def parse_anthropic_thinking(data: dict) -> str:
    """Anthropic SSE 载荷 -> thinking_delta 增量思考文本; 其余返回空串."""
    if data.get("type") != "content_block_delta":
        return ""
    delta = data.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "thinking_delta":
        return ""
    text = delta.get("thinking")
    return text if isinstance(text, str) else ""


def _first_str(source: dict, keys: tuple) -> str:
    """按序取 source 中第一个非空字符串键的值; 无则空串."""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _split_system(messages: list) -> tuple:
    """messages -> (system 文本, 其余消息列表).

    Anthropic 协议 system 是顶层参数; 多条 system 消息以换行拼接.
    """
    system_parts = []
    rest = []
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
        else:
            rest.append(message)
    return "\n".join(system_parts), rest


def _anthropic_usage(raw: dict) -> dict:
    """Anthropic usage(input/output_tokens) -> OpenAI 形态键名."""
    prompt = raw.get("input_tokens")
    completion = raw.get("output_tokens")
    mapped = {}
    if isinstance(prompt, int):
        mapped["prompt_tokens"] = prompt
    if isinstance(completion, int):
        mapped["completion_tokens"] = completion
        mapped["total_tokens"] = prompt + completion \
            if isinstance(prompt, int) else completion
    return mapped


class AiClient:
    """聊天客户端: OpenAI 兼容与 Anthropic 协议(按 base_url 推断)."""

    def __init__(self, config: AiConfig, transport: AiTransport = None):
        self._config = config
        self._transport = transport or UrllibAiTransport(config.timeout)
        self._protocol = "anthropic" \
            if ANTHROPIC_MARK in config.base_url else "openai"

    def chat(self, messages: list, stream: bool = False,
             on_delta=None, think: bool = False,
             on_thinking=None) -> AiReply:
        """一轮对话. messages 为 [{'role','content'}, ...].

        stream=True 时逐 data: 载荷调 on_delta(增量文本)并聚合全文,
        on_delta 为 None 也照常聚合. think=True 请求深度思考: Anthropic
        协议带 thinking 参数, OpenAI 协议不特殊传参(reasoning 模型会
        自行输出); 思考增量经 on_thinking 回调, 全文入 AiReply.thinking.
        网络层故障与非法响应统一转 AiError.
        """
        if not self._config.base_url or not self._config.model:
            raise AiError("config", "base_url 与 model 未配置")
        if self._protocol == "anthropic":
            return self._chat_anthropic(messages, stream, on_delta,
                                        think, on_thinking)
        body = {"model": self._config.model, "messages": messages,
                "stream": bool(stream)}
        if stream:
            return self._chat_stream(body, on_delta, on_thinking)
        response = self._post_json(body)
        self._raise_for_status(response.status, response.text)
        data = self._parse_json(response.text)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiError("protocol", "响应缺少 choices/message 字段") from exc
        if not isinstance(content, str):
            raise AiError("protocol", "响应 content 不是字符串")
        thinking = _first_str(message, ("reasoning_content", "reasoning"))
        usage = data.get("usage")
        finish = choice.get("finish_reason")
        return AiReply(content=content,
                       finish_reason=finish if isinstance(finish, str) else "",
                       usage=dict(usage) if isinstance(usage, dict) else {},
                       model=self._echo_model(data), thinking=thinking)

    # ---- Anthropic Messages 协议 ----

    def _chat_anthropic(self, messages: list, stream: bool,
                        on_delta, think: bool = False,
                        on_thinking=None) -> AiReply:
        """Anthropic 协议一轮对话: system 抽为顶层参数, max_tokens 必填.

        think=True 时请求深度思考(thinking 参数, 预算 THINKING_BUDGET,
        max_tokens 相应放大到大于预算), 思考增量经 on_thinking 回调.
        """
        system, rest = _split_system(messages)
        max_tokens = ANTHROPIC_THINKING_MAX_TOKENS if think \
            else ANTHROPIC_MAX_TOKENS
        body = {"model": self._config.model,
                "max_tokens": max_tokens,
                "messages": rest, "stream": bool(stream)}
        if system:
            body["system"] = system
        if think:
            body["thinking"] = {"type": "enabled",
                                "budget_tokens": THINKING_BUDGET}
        if stream:
            return self._chat_anthropic_stream(body, on_delta, on_thinking)
        response = self._post_json_anthropic(body)
        self._raise_for_status(response.status, response.text)
        data = self._parse_json(response.text)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise AiError("protocol", "响应缺少 content 字段")
        content = "".join(
            block.get("text", "") for block in blocks
            if isinstance(block, dict) and block.get("type") == "text")
        thinking = "".join(
            block.get("thinking", "") for block in blocks
            if isinstance(block, dict) and block.get("type") == "thinking")
        raw_usage = data.get("usage") if isinstance(
            data.get("usage"), dict) else {}
        finish = data.get("stop_reason")
        return AiReply(content=content,
                       finish_reason=finish if isinstance(finish, str) else "",
                       usage=_anthropic_usage(raw_usage),
                       model=self._echo_model(data), thinking=thinking)

    def _chat_anthropic_stream(self, body: dict, on_delta,
                                on_thinking=None) -> AiReply:
        """Anthropic 流式: 逐 data: 载荷按 type 字段分派聚合."""
        try:
            response = self._transport.post_stream(
                self._endpoint(), self._headers(), body)
        except AiNetworkError as exc:
            raise AiError("network", str(exc)) from exc
        if not 200 <= response.status < 300:
            self._raise_for_status(response.status,
                                   "".join(response.lines))
        parts = []
        thoughts = []
        finish = ""
        usage = {}
        model = ""
        for payload in iter_sse_data(response.lines):
            try:
                data = json.loads(payload)
            except ValueError as exc:
                raise AiError("protocol",
                              f"SSE 载荷不是合法 JSON: {payload[:80]}") from exc
            if not isinstance(data, dict):
                raise AiError("protocol", "SSE 载荷不是 JSON 对象")
            kind = data.get("type")
            if kind == "content_block_delta":
                piece = parse_anthropic_delta(data)
                if piece:
                    parts.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                thought = parse_anthropic_thinking(data)
                if thought:
                    thoughts.append(thought)
                    if on_thinking is not None:
                        on_thinking(thought)
            elif kind == "message_delta":
                delta = data.get("delta")
                if isinstance(delta, dict) and isinstance(
                        delta.get("stop_reason"), str):
                    finish = delta["stop_reason"]
                if isinstance(data.get("usage"), dict):
                    usage.update(data["usage"])   # 只带 output, 合并保留 input
            elif kind == "message_start":
                message = data.get("message")
                if isinstance(message, dict):
                    model = self._echo_model(message)
                    if isinstance(message.get("usage"), dict):
                        usage.update(message["usage"])
        return AiReply(content="".join(parts), finish_reason=finish,
                       usage=_anthropic_usage(usage),
                       model=model or self._config.model,
                       thinking="".join(thoughts))

    def _post_json_anthropic(self, body: dict) -> HttpResponse:
        try:
            return self._transport.post_json(
                self._endpoint(), self._headers(), body)
        except AiNetworkError as exc:
            raise AiError("network", str(exc)) from exc

    def _chat_stream(self, body: dict, on_delta,
                     on_thinking=None) -> AiReply:
        try:
            response = self._transport.post_stream(
                self._endpoint(), self._headers(), body)
        except AiNetworkError as exc:
            raise AiError("network", str(exc)) from exc
        if not 200 <= response.status < 300:
            self._raise_for_status(response.status,
                                   "".join(response.lines))
        parts = []
        thoughts = []
        finish = ""
        usage = {}
        model = ""
        for payload in iter_sse_data(response.lines):
            try:
                data = json.loads(payload)
            except ValueError as exc:
                raise AiError("protocol",
                              f"SSE 载荷不是合法 JSON: {payload[:80]}") from exc
            if not isinstance(data, dict):
                raise AiError("protocol", "SSE 载荷不是 JSON 对象")
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(
                    choices[0], dict):
                delta = choices[0].get("delta")
                if isinstance(delta, dict):
                    piece = _first_str(delta, ("content",))
                    if piece:
                        parts.append(piece)
                        if on_delta is not None:
                            on_delta(piece)
                    thought = _first_str(delta, ("reasoning_content",
                                                 "reasoning"))
                    if thought:
                        thoughts.append(thought)
                        if on_thinking is not None:
                            on_thinking(thought)
                reason = choices[0].get("finish_reason")
                if isinstance(reason, str) and reason:
                    finish = reason
            if isinstance(data.get("usage"), dict):
                usage = data["usage"]
            model = self._echo_model(data) or model
        return AiReply(content="".join(parts), finish_reason=finish,
                       usage=usage, model=model or self._config.model,
                       thinking="".join(thoughts))

    def _post_json(self, body: dict) -> HttpResponse:
        try:
            return self._transport.post_json(
                self._endpoint(), self._headers(), body)
        except AiNetworkError as exc:
            raise AiError("network", str(exc)) from exc

    def _endpoint(self) -> str:
        if self._protocol == "anthropic":
            return self._config.base_url.rstrip("/") + "/v1/messages"
        return self._config.base_url.rstrip("/") + "/chat/completions"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if not self._config.api_key:
            return headers
        if self._protocol == "anthropic":
            headers["x-api-key"] = self._config.api_key
            headers["anthropic-version"] = ANTHROPIC_VERSION
        else:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _echo_model(self, data: dict) -> str:
        """取响应回显的模型名; 缺失返回空串."""
        model = data.get("model")
        return model if isinstance(model, str) else ""

    @staticmethod
    def _parse_json(text: str) -> dict:
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AiError("protocol", "响应体不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise AiError("protocol", "响应体不是 JSON 对象")
        return data

    @classmethod
    def _raise_for_status(cls, status: int, text: str) -> None:
        """2xx 放行; 其余按状态码分类抛 AiError, 消息优先取 error.message."""
        if 200 <= status < 300:
            return
        message = cls._error_message(text) or text[:200]
        if status in (401, 403):
            raise AiError("auth", f"鉴权失败(HTTP {status}): {message}", status)
        if status == 429:
            raise AiError("rate_limit",
                          f"请求过于频繁(HTTP 429): {message}", status)
        if status >= 500:
            raise AiError("server", f"服务端错误(HTTP {status}): {message}",
                          status)
        raise AiError("protocol", f"意外的 HTTP 状态 {status}: {message}",
                      status)

    @staticmethod
    def _error_message(text: str) -> str:
        """从错误体提取 error.message / message 字段; 无则空串."""
        try:
            data = json.loads(text)
        except ValueError:
            return ""
        if not isinstance(data, dict):
            return ""
        error = data.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        if isinstance(data.get("message"), str):
            return data["message"]
        return ""


class AiConfigStore:
    """data/ai-config.json: base_url/model/api_key 三键.

    原子写 + 0o600(campus SessionStore 同款); 缺失/损坏返回空 dict;
    只认 CONFIG_KEYS 三键, 其余键忽略.
    """

    def __init__(self, path: str):
        self._path = path

    def load(self) -> dict:
        """读配置; 缺失/损坏/结构非法返回空 dict."""
        profiles, active = self.load_profiles()
        return dict(profiles.get(active, {}))

    def save(self, values: dict) -> None:
        """合并写入(values 覆盖同键旧值); OSError 上抛调用方."""
        profiles, active = self.load_profiles()
        current = dict(profiles.get(active, {}))
        current.update({key: values[key] for key in CONFIG_KEYS
                        if key in values})
        self.save_profile(active or "默认配置", current)

    def clear_key(self) -> None:
        """删除 api_key 键后重写(base_url/model 保留)."""
        _profiles, active = self.load_profiles()
        if active:
            self.clear_profile_key(active)

    def load_profiles(self) -> tuple:
        """返回 ({配置组名: 三键配置}, 当前组名).

        旧版单组文件自动呈现为默认组, 保持 load() 兼容; 空配置组名为
        "默认配置", 首组自动成为当前组.
        """
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}, ""
        if not isinstance(data, dict):
            return {}, ""
        raw_profiles = data.get("profiles")
        if isinstance(raw_profiles, dict):
            profiles = {}
            for name, values in raw_profiles.items():
                if not isinstance(name, str) or not name.strip() \
                        or not isinstance(values, dict):
                    continue
                cleaned = {key: values[key] for key in CONFIG_KEYS
                           if isinstance(values.get(key), str)}
                profiles[name.strip()] = cleaned
            active = data.get("active_profile", "")
            if not isinstance(active, str) or active not in profiles:
                active = next(iter(profiles), "")
            return profiles, active
        legacy = {key: data[key] for key in CONFIG_KEYS
                  if isinstance(data.get(key), str)}
        if legacy:
            return {"默认配置": legacy}, "默认配置"
        return {}, ""

    def save_profile(self, name: str, values: dict, activate: bool = True) -> None:
        """新增或覆盖一组配置, 默认可选设为当前组."""
        name = (name or "").strip()
        if not name:
            raise ValueError("配置组名不能为空")
        profiles, active = self.load_profiles()
        cleaned = {key: values[key] for key in CONFIG_KEYS if key in values}
        profiles[name] = cleaned
        if activate or not active:
            active = name
        self._write_profiles(profiles, active)

    def delete_profile(self, name: str) -> None:
        """删除一组配置; 当前组被删时选择剩余首组."""
        profiles, active = self.load_profiles()
        if name not in profiles:
            return
        del profiles[name]
        if active == name:
            active = next(iter(profiles), "")
        self._write_profiles(profiles, active)

    def set_active_profile(self, name: str) -> None:
        """切换当前配置组; 组不存在时不改动."""
        profiles, _active = self.load_profiles()
        if name in profiles:
            self._write_profiles(profiles, name)

    def clear_profile_key(self, name: str) -> None:
        """清除指定配置组的 api_key, 保留其他字段."""
        profiles, active = self.load_profiles()
        if name in profiles:
            profiles[name].pop("api_key", None)
            self._write_profiles(profiles, active)

    def _write_profiles(self, profiles: dict, active: str) -> None:
        """多组结构落盘, 与单组配置共用原子写与权限约束."""
        self._rewrite({"profiles": profiles, "active_profile": active})

    def _rewrite(self, values: dict) -> None:
        """全量重写为给定键值; 原子写 + 0o600."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)
