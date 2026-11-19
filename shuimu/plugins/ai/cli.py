"""AI 工具插件的 CLI 增强: ai-* 子命令的注册与执行.

专属逻辑跟插件包走, cli/main.py 只做两行接线(注册与分发). API Key
交互输入(getpass), 绝不走 argv.
"""

import getpass
import sys

from shuimu.core.controller import Controller

AI_COMMANDS = ("ai-status", "ai-ask", "ai-config", "ai-history", "ai-clear")

KEY_CLEAR_WORD = "CLEAR"


def build_ai_subcommands(sub) -> None:
    """注册 AI 相关子命令(controller AI 动作的薄封装)."""
    sub.add_parser("ai-status", help="AI 配置与历史概况(零联网)")
    p_ask = sub.add_parser("ai-ask", help="提问(默认流式输出)")
    p_ask.add_argument("question", help="问题内容")
    p_ask.add_argument("--course", type=int, help="课程 id(纳入该课资源)")
    p_ask.add_argument("--with-learn", action="store_true",
                       help="附带学堂缓存(公告/作业描述)")
    p_ask.add_argument("--no-stream", action="store_true",
                       help="关闭流式, 等全部生成后一次性输出")
    p_ask.add_argument("--think", action="store_true",
                       help="深度思考(思考流打到 stderr, 回答仍走 stdout)")
    p_cfg = sub.add_parser("ai-config", help="查看/保存 AI 配置")
    p_cfg.add_argument("--base-url", help="OpenAI 兼容服务地址")
    p_cfg.add_argument("--model", help="模型名")
    p_cfg.add_argument("--show", action="store_true", help="只查看不修改")
    p_hist = sub.add_parser("ai-history", help="最近对话会话")
    p_hist.add_argument("--limit", type=int, default=20, help="列出条数")
    p_hist.add_argument("--session", type=int, help="展开该会话的完整消息")
    p_clear = sub.add_parser("ai-clear", help="清除对话历史")
    p_clear.add_argument("--session", type=int, help="只清除该会话")
    p_clear.add_argument("--yes", action="store_true", help="跳过确认")


def run_ai_command(ctl: Controller, args) -> int:
    """执行 AI 相关子命令; 供 cli/main.py 的分发调用."""
    if args.command == "ai-status":
        return _emit(ctl.ai_status())
    if args.command == "ai-ask":
        return _ask(ctl, args)
    if args.command == "ai-config":
        return _config(ctl, args)
    if args.command == "ai-history":
        return _history(ctl, args)
    return _clear(ctl, args)


def _emit(result) -> int:
    """打印 ActionResult 并按成败给退出码(与 cli.main._emit 同契约)."""
    print(result.message, file=sys.stderr if not result.ok else sys.stdout)
    return 0 if result.ok else 2


def _ask(ctl: Controller, args) -> int:
    """ai-ask: 流式逐段打印到 stdout(思考与统计走 stderr, stdout 纯回答)."""
    course_ids = [args.course] if args.course else None

    def on_thinking(piece: str) -> None:
        print(piece, end="", file=sys.stderr, flush=True)

    if args.no_stream:
        result = ctl.ai_ask(args.question, course_ids=course_ids,
                            include_learn=args.with_learn,
                            think=args.think)
        if not result.ok:
            return _emit(result)
        if result.detail.get("thinking"):
            print(f"[思考] {result.detail['thinking']}",
                  file=sys.stderr)
        print(result.detail["answer"])
    else:
        result = ctl.ai_ask(args.question, course_ids=course_ids,
                            include_learn=args.with_learn, stream=True,
                            think=args.think,
                            on_thinking=on_thinking,
                            on_delta=lambda piece: print(
                                piece, end="", flush=True))
        if not result.ok:
            return _emit(result)
        print()
    items = result.detail.get("context_items") or []
    skipped = [item["label"] for item in items if item.get("skipped")]
    note = f"[上下文 {len(items)} 条 {result.detail.get('context_chars', 0)}" \
           f" 字符, 模型 {result.detail.get('model', '?')}]"
    if skipped:
        note += f" 未纳入: {', '.join(skipped[:5])}"
    print(note, file=sys.stderr)
    return 0


def _config(ctl: Controller, args) -> int:
    """ai-config: 查看或保存配置; key 经 getpass, 不走 argv."""
    if args.show:
        detail = ctl.ai_status().detail
        print(f"插件状态: {detail['plugin']}")
        print(f"服务地址: {detail['base_url'] or '(未配置)'}")
        print(f"模型名: {detail['model'] or '(未配置)'}")
        print(f"API Key: {'已保存' if detail['has_key'] else '未保存'}")
        print(f"历史: {detail['sessions']} 会话 {detail['messages']} 条")
        return 0
    base_url, model = args.base_url, args.model
    if base_url is None and model is None:
        base_url = input("服务地址(回车保持不变): ").strip() or None
        model = input("模型名(回车保持不变): ").strip() or None
    raw = getpass.getpass(
        f"API Key(留空保持不变, 输入 {KEY_CLEAR_WORD} 清除): ")
    api_key = None
    if raw == KEY_CLEAR_WORD:
        api_key = ""
    elif raw:
        api_key = raw
    return _emit(ctl.ai_config_set(base_url=base_url, model=model,
                                   api_key=api_key))


def _history(ctl: Controller, args) -> int:
    """ai-history: 会话列表或单会话完整消息."""
    if args.session is not None:
        rows = ctl.storage.ai_list_messages(args.session)
        if not rows:
            print(f"会话 #{args.session} 不存在或无消息", file=sys.stderr)
            return 2
        for row in rows:
            prefix = "我: " if row["role"] == "user" else "AI: "
            print(f"{prefix}{row['content']}")
        return 0
    result = ctl.ai_history(limit=args.limit)
    sessions = result.detail.get("sessions") or []
    if not sessions:
        print("(空: 还没有对话)")
        return 0
    for row in sessions:
        print(f"#{row['id']}  {row['title']}  ({row['message_count']} 条)")
    return 0


def _clear(ctl: Controller, args) -> int:
    """ai-clear: 清除历史(默认需输入 y 确认, --yes 跳过)."""
    if not args.yes:
        scope = f"会话 #{args.session}" if args.session else "全部会话"
        answer = input(f"清除 {scope} 的对话历史? 输入 y 确认: ")
        if answer.strip().lower() != "y":
            print("已取消")
            return 0
    return _emit(ctl.ai_clear_history(session_id=args.session))
