# 插件: AI 工具(ai)

把课程资料交给大模型, 针对课程问题给出回复. 兼容任意 **OpenAI 兼容服务**(智谱 / DeepSeek / Kimi / 通义 / OpenAI / 本地 Ollama 等), 以可选 network 插件形态提供: **默认禁用, 启用即授权联网**; 核心功能保持零网络.

## 前提与启用

```bash
python cli.py plugin-enable ai --yes
```

需要一个 OpenAI 兼容服务或 Anthropic 协议服务的 API Key(Ollama 本地服务可免). 常用服务地址(base_url)与模型示例:

| 服务 | base_url | 协议 | 模型示例 |
|---|---|---|---|
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | OpenAI 兼容 | `glm-5.3` |
| 智谱 AI (Claude Code 资源包) | `https://open.bigmodel.cn/api/anthropic` | Anthropic | `glm-5.3` |
| DeepSeek | `https://api.deepseek.com/v1` | OpenAI 兼容 | `deepseek-chat` |
| Kimi (月之暗面) | `https://api.moonshot.cn/v1` | OpenAI 兼容 | `moonshot-v1-8k` |
| Ollama 本地 | `http://localhost:11434/v1` | OpenAI 兼容 | `qwen3:8b` |
| OpenAI | `https://api.openai.com/v1` | OpenAI 兼容 | `gpt-4o-mini` |

协议按 base_url 自动推断: 地址含 `/anthropic` 走 Anthropic Messages 协议, 其余走 OpenAI 兼容协议, 无需额外配置. 注意智谱两种端点的资源包**不通用**: Claude Code 订阅的 key 只在 `/anthropic` 端点有额度, 请按自己 key 的类型选 base_url; 模型名用 API 名(如 `glm-5.3`, 完整清单见 `GET <base_url>/models`), 不要填 `GLM-5.3[1m]` 这类客户端别名.

## 配置

```bash
python cli.py ai-config                          # 交互式: 服务地址/模型名/API Key
python cli.py ai-config --base-url https://api.deepseek.com/v1 --model deepseek-chat
python cli.py ai-config --show                   # 查看当前配置
```

- 三项配置存本机 `data/ai-config.json`(**权限 0o600, 不入仓库**); Key 清除: 交互时输入 `CLEAR`
- 多组配置时文件含 `profiles` 与 `active_profile`, CLI 使用当前组; 配置组切换/删除在 GUI 配置页操作
- 调优项(上下文字符预算/单文件上限/PDF 页数/超时/历史保留条数)在项目根 `config.yaml` 的 `ai` 段, 有默认值

**GUI**: 右栏"AI"页签, 未配置时在页内直接填表保存; 已配置时点工具行"设置"返回配置页. 配置页支持多组服务配置: 顶部"配置组"下拉切换当前组, "另存为"把表单保存为新组, "删除"移除当前组及其已存密钥. 每组保存服务地址/模型/API Key; 问答与 CLI 始终使用当前组. 旧版单组配置自动归入"默认配置".

## 提问与上下文

```bash
python cli.py ai-ask "特征值和特征向量的几何意义是什么"            # 不附带资料
python cli.py ai-ask "总结这门课的作业要求" --course 1            # 附带课程 1 的资料
python cli.py ai-ask "整理最近的公告要点" --course 1 --with-learn  # 附带学堂缓存
python cli.py ai-ask "..." --no-stream                            # 关闭流式输出
python cli.py ai-ask "..." --think                                # 深度思考(思考流走 stderr)
```

- 上下文按勾选构建: 课程资源(文件索引 + 链接) + 学堂缓存(`--with-learn`, 关联课程的公告与作业描述)
- 文本提取: 纯文本/Markdown/代码直读; PDF 经 pypdf 按页提取(加密或扫描件会跳过并告知); HTML 转文本; docx/pptx 粗提文字
- 预算控制: 总字符预算(默认 60000)内先到先得, 单文件上限 20000 字符, 超限文件在结果里列名告知
- CLI 流式输出: 回答逐段打印到 stdout(便于管道), 思考流与统计信息走 stderr

**GUI**: 右栏"AI"页签 = 对话视图(消息流 + 输入卡片, Ctrl+Enter 发送, 流式追加), 上方"上下文"下拉选择 不附带/某课程/全部课程, 可勾选"学堂"与"思考"; 顶部"历史"打开会话管理, 可打开选中会话、仅删除选中会话, 或单独确认后清空全部.

**深度思考**: 勾选(或 --think)后模型先思考再作答. GUI 中思考流进入对话区下方的折叠面板(流式时自动展开, 完成后收起为"思考过程 (N 字)", 点击可回看; 段落按模型原文保留), CLI 思考流走 stderr. 思考不持久化, 历史回放只含问答本身.

**渲染与用量**: 回答按 Markdown 渲染(标题/列表/代码块/表格; 数学公式按原文等宽显示, 不渲染 LaTeX); 输入框下方常驻本会话累计 tokens(配置 `ai.context_tokens` 后同时显示可用上限, 如 `128000`).

## 历史管理

```bash
python cli.py ai-history                 # 最近会话列表
python cli.py ai-history --session 1     # 展开某会话完整消息
python cli.py ai-clear --yes             # 清除全部历史(--session N 清单会话)
```

对话历史存本机 SQLite(`data/shuimu.db` 的 ai_sessions/ai_messages 表), 超出保留条数(默认 200)自动删最旧.

## 数据与隐私

- **API Key 只存本机 `data/ai-config.json`(0o600), 不入仓库, 不入操作留痕**
- 操作留痕只记元数据: 问题前 60 字/模型名/上下文统计/耗时; 完整对话只存本机数据库
- 通用插件入口同样可用: `plugin-run ai --set action=ask --set question=...`(`--set api_key=...` 传入的 Key 仅本次使用, 不落盘)
- 提问内容与勾选的课程资料会发送到所配置的 AI 服务; 不想上传的资料不要勾选对应课程
