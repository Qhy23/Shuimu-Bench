"""AI 工具业务动作: Controller 的混入部分(门禁/配置/问答/历史).

依赖宿主属性: storage, plugins, config, _ai_transport_factory,
_ai_config_path. 留痕纪律: api_key 与完整对话绝不入 operations, 问答
留痕只记元数据(问题前 60 字/模型/上下文统计/耗时); 完整对话只在
ai_messages 表.
"""

import time

from shuimu.core.actions import ActionResult
from shuimu.core.ai import (
    AiClient,
    AiConfig,
    AiConfigStore,
    AiError,
)
from shuimu.core.ai_context import build_context

SYSTEM_PROMPT = ("你是水木工作台里的课程学习助手. 请依据下面提供的课程资料"
                 "回答问题; 资料不足以回答时如实说明.\n"
                 "公式只能使用行内 $...$ 或独立行 $$...$$ 的 LaTeX 环境;"
                 "不要使用 \\[...\\], \\(...\\) 或其他公式环境.\n\n")
AI_ERROR_MESSAGES = {
    "auth": "鉴权失败: 请检查 API Key 与服务地址",
    "rate_limit": "请求过于频繁, 请稍后再试",
    "server": "AI 服务端错误, 请稍后再试",
    "network": "暂时无法访问 AI 服务(网络异常)",
    "protocol": "AI 服务返回了无法解析的响应",
}


class AiActions:
    """AI 工具动作集合(由 Controller 继承)."""

    def _ai_gate(self):
        """AI 插件门禁: 未启用/不可用时返回失败结果(零联网), 可用为 None."""
        record = self.plugins.get("ai")
        if record is None:
            return ActionResult(False, "AI 插件未安装")
        if record.status == "broken":
            return ActionResult(False, f"AI 插件损坏: {record.error}")
        if record.status == "disabled":
            return ActionResult(False, "AI 插件未启用(需网络授权)")
        return None

    def _ai_store(self) -> AiConfigStore:
        """用户级配置存储(data/ai-config.json)."""
        return AiConfigStore(self._ai_config_path)

    def _ai_tuning(self) -> dict:
        """config.yaml 的 ai 调优段(容错读取)."""
        tuning = self.config.get("ai", {})
        return tuning if isinstance(tuning, dict) else {}

    def _ai_config(self, override_key: str = None) -> AiConfig:
        """组装 AiConfig: store 的用户配置 + config 调优项.

        override_key 非空时优先于已存 key(通用 plugin-run 通道一次性使用,
        不落盘). base_url/model 未配置抛 AiError('config').
        """
        stored = self._ai_store().load()
        base_url = (stored.get("base_url") or "").strip()
        model = (stored.get("model") or "").strip()
        api_key = override_key or stored.get("api_key") or ""
        if not base_url or not model:
            raise AiError("config",
                          "AI 未配置: 请先填写服务地址与模型名"
                          "(GUI 右栏 AI 页或 cli.py ai-config)")
        tuning = self._ai_tuning()
        return AiConfig(base_url=base_url, api_key=api_key, model=model,
                        timeout=int(tuning.get("timeout_seconds", 120)))

    def ai_status(self) -> ActionResult:
        """只读: 插件态 + 配置态 + 历史计数(零联网不留痕)."""
        record = self.plugins.get("ai")
        stored = self._ai_store().load()
        configured = bool((stored.get("base_url") or "").strip()) and \
            bool((stored.get("model") or "").strip())
        sessions = self.storage.ai_list_sessions(limit=1000)
        message_count = sum(row["message_count"] for row in sessions)
        detail = {
            "plugin": None if record is None else record.status,
            "base_url": stored.get("base_url", ""),
            "model": stored.get("model", ""),
            "has_key": bool(stored.get("api_key")),
            "configured": configured,
            "sessions": len(sessions),
            "messages": message_count,
        }
        profiles, active = self._ai_store().load_profiles()
        detail["profiles"] = list(profiles)
        detail["active_profile"] = active
        state = "已配置" if configured else "未配置"
        message = (f"AI 插件: {detail['plugin']}, {state}, "
                   f"历史 {len(sessions)} 会话 {message_count} 条")
        return ActionResult(True, message, detail)

    def ai_config_set(self, base_url: str = None, model: str = None,
                      api_key: str = None) -> ActionResult:
        """保存用户级配置(data/ai-config.json), 留痕(只记字段名).

        各参数 None 表示不改动; api_key 传空串表示清除已存密钥.
        """
        store = self._ai_store()
        values = {}
        fields = []
        if base_url is not None:
            values["base_url"] = base_url.strip()
            fields.append("base_url")
        if model is not None:
            values["model"] = model.strip()
            fields.append("model")
        key_cleared = False
        if api_key is not None:
            fields.append("api_key")
            if api_key == "":
                store.clear_key()
                key_cleared = True
            else:
                values["api_key"] = api_key.strip()
        if values:
            store.save(values)
        self.storage.log_operation(
            "ai_config", params={"fields": fields},
            detail={"key_cleared": key_cleared})
        return ActionResult(True, "AI 配置已保存",
                            {"fields": fields, "key_cleared": key_cleared})

    def ai_profile_switch(self, name: str) -> ActionResult:
        """切换当前 AI 配置组(零联网, 留痕不含密钥)."""
        store = self._ai_store()
        profiles, _active = store.load_profiles()
        if name not in profiles:
            return ActionResult(False, "配置组不存在")
        store.set_active_profile(name)
        self.storage.log_operation("ai_profile_switch",
                                   detail={"profile": name})
        return ActionResult(True, f"已切换到配置组「{name}」",
                            {"profile": name})

    def ai_profile_delete(self, name: str) -> ActionResult:
        """删除指定 AI 配置组(零联网, 留痕不含密钥)."""
        store = self._ai_store()
        profiles, _active = store.load_profiles()
        if name not in profiles:
            return ActionResult(False, "配置组不存在")
        store.delete_profile(name)
        self.storage.log_operation("ai_profile_delete",
                                   detail={"profile": name})
        return ActionResult(True, f"已删除配置组「{name}」",
                            {"profile": name})

    def ai_ask(self, question: str, course_ids=None, resource_ids=None,
               include_learn: bool = False, session_id=None,
               stream: bool = False, on_delta=None, think: bool = False,
               on_thinking=None) -> ActionResult:
        """提问: 构建上下文 -> 取会话历史 -> 调模型 -> 历史落库, 留痕.

        think=True 请求深度思考, 思考增量经 on_thinking 回调, 全文进
        detail["thinking"]; 思考不持久化(历史回放不含, 与 CLI 工具同款).
        留痕只记元数据(params: 问题前 60 字/课程 id/是否含学堂缓存;
        detail: 模型/上下文统计/耗时/会话 id/tokens), 完整对话只进
        ai_messages.
        """
        gate = self._ai_gate()
        if gate is not None:
            return gate
        question = (question or "").strip()
        if not question:
            return ActionResult(False, "问题不能为空", {"state": "empty"})
        try:
            config = self._ai_config()
        except AiError as exc:
            return ActionResult(False, str(exc), {"state": "config"})
        tuning = self._ai_tuning()
        started = time.time()
        context = build_context(
            self.storage, course_ids=course_ids, resource_ids=resource_ids,
            include_learn=include_learn,
            budget=int(tuning.get("max_context_chars", 60000)),
            per_file_limit=int(tuning.get("per_file_chars", 20000)),
            max_pdf_pages=int(tuning.get("max_pdf_pages", 30)))
        messages = self._ai_messages(question, context.text, session_id)
        client = AiClient(config, self._ai_transport_factory())
        try:
            reply = client.chat(messages, stream=stream, on_delta=on_delta,
                                think=think, on_thinking=on_thinking)
        except AiError as exc:
            return self._ai_fail(exc, question, course_ids, include_learn)
        elapsed_ms = int((time.time() - started) * 1000)
        session_id = self.storage.ai_add_round(session_id, question,
                                               reply.content)
        pruned = self.storage.ai_prune_history(
            int(tuning.get("keep_messages", 200)))
        log_detail = {
            "model": reply.model,
            "context_items": len(context.items),
            "context_chars": context.total_chars,
            "elapsed_ms": elapsed_ms,
            "session_id": session_id,
            "tokens": (reply.usage or {}).get("total_tokens"),
            "thought": bool(reply.thinking),
        }
        if pruned:
            log_detail["pruned"] = pruned
        self.storage.log_operation(
            "ai_ask",
            params={"question_head": question[:60],
                    "course_ids": list(course_ids or []),
                    "with_learn": bool(include_learn)},
            detail=log_detail)
        return ActionResult(True, "已回答",
                            {"answer": reply.content,
                             "session_id": session_id,
                             "model": reply.model,
                             "elapsed_ms": elapsed_ms,
                             "usage": dict(reply.usage),
                             "thinking": reply.thinking,
                             "context_items": [_item_dict(item)
                                               for item in context.items],
                             "context_chars": context.total_chars})

    def _ai_messages(self, question: str, context_text: str,
                     session_id) -> list:
        """拼对话消息: 资料在 system, 会话历史次之, 本轮问题最后."""
        messages = []
        if context_text:
            messages.append({"role": "system",
                             "content": SYSTEM_PROMPT + context_text})
        if session_id is not None:
            for row in self.storage.ai_recent_messages(session_id):
                messages.append({"role": row["role"],
                                 "content": row["content"]})
        messages.append({"role": "user", "content": question})
        return messages

    def _ai_fail(self, exc: AiError, question: str, course_ids,
                 include_learn: bool) -> ActionResult:
        """运行失败转结果; 运行类失败留痕 status=failed(config 类不留)."""
        detail = {"state": exc.kind, "status_code": exc.status}
        if exc.kind != "config":
            self.storage.log_operation(
                "ai_ask", status="failed",
                params={"question_head": question[:60],
                        "course_ids": list(course_ids or []),
                        "with_learn": bool(include_learn)},
                detail={"error": exc.kind, "message": str(exc)[:200]})
        message = AI_ERROR_MESSAGES.get(
            exc.kind, f"AI 调用失败: {exc}")
        detail["message"] = str(exc)[:200]
        return ActionResult(False, message, detail)

    def ai_history(self, limit: int = 20) -> ActionResult:
        """最近会话列表(只读不留痕)."""
        sessions = self.storage.ai_list_sessions(limit)
        return ActionResult(True, f"{len(sessions)} 个会话",
                            {"sessions": sessions})

    def ai_clear_history(self, session_id=None) -> ActionResult:
        """清除全部或单会话历史, 留痕."""
        removed = self.storage.ai_clear_history(session_id)
        self.storage.log_operation(
            "ai_clear_history",
            params={"session_id": session_id} if session_id else None,
            detail={"removed": removed})
        scope = f"会话 #{session_id}" if session_id else "全部会话"
        return ActionResult(True, f"已清除 {scope} ({removed} 个)",
                            {"removed": removed})


def _item_dict(item) -> dict:
    """ContextItem -> 可序列化 dict(给 GUI/CLI 展示)."""
    return {"label": item.label, "source": item.source, "kind": item.kind,
            "chars": item.chars, "truncated": item.truncated,
            "skipped": item.skipped}
