"""AI 工具插件入口: 复用 core/ai 客户端与 ai_context 编排, 按 action 分派.

主通道是 controller.ai_* 动作(GUI 右栏 AI 页签与 CLI ai-* 子命令,
对话体验更好); 本入口保持与通用插件框架的对接(plugin-run ai).
api_key 经 secret 参数传入时仅本次使用, 不落盘不入报告字段.
"""

from shuimu.core.ai import (
    AiClient,
    AiConfig,
    AiConfigStore,
    AiError,
    UrllibAiTransport,
)
from shuimu.core.ai_actions import SYSTEM_PROMPT
from shuimu.core.ai_context import build_context
from shuimu.core.config import load_config
from shuimu.core.paths import default_ai_config_path, default_db_path
from shuimu.core.pluginapi import (
    PluginJob,
    PluginReport,
    finding,
    make_report,
)
from shuimu.core.storage import Storage

ANSWER_DETAIL_LIMIT = 400   # finding.detail 上限(防全量回答间接入留痕)


def _config_store():
    """用户级配置存储(测试可替换)."""
    return AiConfigStore(default_ai_config_path())


def _transport_factory():
    """生产传输层(测试可替换为 MemoryAiTransport)."""
    return UrllibAiTransport()


def _storage() -> Storage:
    """工作台数据库(测试可替换为 tmp 库)."""
    return Storage(default_db_path())


def run(job: PluginJob) -> PluginReport:
    """按 params.action 分派: status(只读) / ask / history / clear."""
    action = job.params.get("action", "status")
    if action == "ask":
        return _ask(job.params)
    if action == "history":
        return _history(job.params)
    if action == "clear":
        return _clear()
    return _status()


def _status() -> PluginReport:
    """只读配置态与历史计数(不联网)."""
    stored = _config_store().load()
    configured = bool(stored.get("base_url")) and bool(stored.get("model"))
    storage = _storage()
    try:
        sessions = storage.ai_list_sessions(limit=1000)
        message_count = sum(row["message_count"] for row in sessions)
    finally:
        storage.close()
    findings = [
        finding("ok" if configured else "warn", "config",
                "已配置" if configured else "未配置(缺服务地址或模型名)",
                f"服务地址: {stored.get('base_url') or '(空)'}; "
                f"模型: {stored.get('model') or '(空)'}; "
                f"密钥: {'已保存' if stored.get('api_key') else '未保存'}"),
        finding("ok", "history", f"历史 {len(sessions)} 会话 {message_count} 条"),
    ]
    state = "已配置" if configured else "未配置"
    return make_report(findings, summary=f"AI 插件: {state}")


def _ask(params: dict) -> PluginReport:
    """单轮问答: 上下文 -> 非流式调用 -> 历史落库(通用通道无流式消费方)."""
    question = (params.get("question") or "").strip()
    if not question:
        return make_report(
            [finding("error", "input", "缺少问题",
                     "action=ask 需要 question 参数")],
            summary="缺少问题")
    stored = _config_store().load()
    base_url = (stored.get("base_url") or "").strip()
    model = (stored.get("model") or "").strip()
    api_key = params.get("api_key") or stored.get("api_key") or ""
    if not base_url or not model:
        return make_report(
            [finding("error", "config", "AI 未配置",
                     "先经 GUI 右栏 AI 页或 cli.py ai-config 保存"
                     " 服务地址与模型名")],
            summary="AI 未配置")
    tuning = load_config().get("ai", {})
    timeout = int(tuning.get("timeout_seconds", 120))
    course_ids = [params["course_id"]] if params.get("course_id") else None
    storage = _storage()
    try:
        context = build_context(
            storage, course_ids=course_ids,
            include_learn=bool(params.get("with_learn")),
            budget=int(tuning.get("max_context_chars", 60000)),
            per_file_limit=int(tuning.get("per_file_chars", 20000)),
            max_pdf_pages=int(tuning.get("max_pdf_pages", 30)))
        messages = []
        if context.text:
            messages.append({"role": "system",
                             "content": SYSTEM_PROMPT + context.text})
        messages.append({"role": "user", "content": question})
        client = AiClient(AiConfig(base_url, api_key, model, timeout),
                          _transport_factory())
        reply = client.chat(messages)
        storage.ai_add_round(None, question, reply.content)
        storage.ai_prune_history(int(tuning.get("keep_messages", 200)))
    except AiError as exc:
        return make_report(
            [finding("error", _error_check(exc.kind), "AI 调用失败",
                     str(exc))],
            summary="AI 调用失败")
    finally:
        storage.close()
    findings = [
        finding("ok", "answer", reply.content[:ANSWER_DETAIL_LIMIT],
                "" if len(reply.content) <= ANSWER_DETAIL_LIMIT
                else f"(回答共 {len(reply.content)} 字, 已截断)"),
        finding("ok", "context",
                f"上下文 {len(context.items)} 条 {context.total_chars} 字符",
                "; ".join(item.label for item in context.items[:10])
                or "未附带资料"),
    ]
    return make_report(findings, summary="已回答")


def _error_check(kind: str) -> str:
    """AiError kind -> finding check 标识."""
    return f"ai_{kind}"


def _history(params: dict) -> PluginReport:
    """最近会话列表(只读)."""
    limit = int(params.get("limit", 20) or 20)
    storage = _storage()
    try:
        sessions = storage.ai_list_sessions(limit)
    finally:
        storage.close()
    if not sessions:
        return make_report([finding("ok", "history", "暂无对话历史")],
                           summary="暂无对话历史")
    findings = [
        finding("ok", "history", f"#{row['id']} {row['title']}",
                f"{row['message_count']} 条消息")
        for row in sessions
    ]
    return make_report(findings, summary=f"{len(sessions)} 个会话")


def _clear() -> PluginReport:
    """清除全部对话历史."""
    storage = _storage()
    try:
        removed = storage.ai_clear_history()
    finally:
        storage.close()
    return make_report(
        [finding("ok", "history", f"已清除 {removed} 个会话")],
        summary=f"已清除 {removed} 个会话")
