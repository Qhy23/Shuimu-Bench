# AGENTS.md -- 水木工作台(ShuimuBench)

## 项目概述

本地优先的清华学习科研工作台. 以文件资源管理器为基底, 按课程组织资源(文件/文件夹引用 + 网页链接), 常用操作以插件形式执行并全程留痕. 清华大学《AI 编程进阶: 方法与实践》课程项目.

## 技术栈与环境

- 语言: Python 3.12
- GUI: PySide6 (Qt), 中栏文件浏览用 QFileSystemModel 直读磁盘
- 存储: SQLite(标准库 sqlite3, WAL 模式), 不用 ORM
- 测试: pytest + pytest-qt, 无头环境设 `QT_QPA_PLATFORM=offscreen`
- 开发环境: Linux/WSL2 (WSLg); 第一版交付 Linux, Windows 打包(PyInstaller, 需 Windows 侧构建)后续处理
- 运行时零网络零模型依赖

## 代码与文档约定(必须遵守)

- **标点**: 代码, Markdown, commit message 中全部使用半角标点(与用户的聊天不受此限).
- **Commit**: 遵循 Conventional Commits 1.0.0 (https://www.conventionalcommits.org/zh-hans/v1.0.0/), `<description>` 使用中文. 示例: `feat: 新增课程工作区的 SQLite 索引`.
- **lint**: 提交前运行 `.venv/bin/pylint --rcfile=../.pylintrc <changed-files>`; `.pylintrc` 位于课程根目录(项目外一层), PySide6 的 E0611 由其 extension-pkg-allow-list 处理, 不要用行内 disable 绕过.
- **Commit 署名**: AI 参与生成的提交在 message 末尾加 trailer, 如实注明本次实际使用的 AI 工具与模型, 如 `Co-Authored-By: OpenCode (Kimi K3) <opencode-agent[bot]@users.noreply.github.com>`, `Co-Authored-By: Claude Code (GLM-5.3) <noreply@anthropic.com>`.
- **安全**: 不提交 API Key 等凭据; `.env`, `.venv/`, `*.db` 已在 .gitignore.

## 范围控制

- MVP: 三栏主窗口 + 课程工作区(SQLite 索引) + 文件浏览 + 本地日程 + 操作记录.
- 本学期不做: 插件市场, PDF 阅读器, 任何学校网站在线接入(仅网页链接资源), 模拟登录.
- 插件框架在 MVP 之后开发; 详细规划见 ../.claude/project-proposal.md.

## 常用命令

```bash
source .venv/bin/activate
python main.py                                # 运行 GUI
python cli.py --help                          # CLI(无头/外部测试用)
QT_QPA_PLATFORM=offscreen pytest              # 测试
pylint --rcfile=../.pylintrc main.py shuimu/ tests/   # lint(配置在课程根目录)
```

## 数据组织

- 数据库: `project/data/shuimu.db`(SQLite, WAL; 已 gitignore).
- 三表: `courses`(课程), `resources`(索引: kind 为 file/dir/link, 文件类统一存绝对路径 + size/mtime), `operations`(操作留痕, params/detail 为 JSON).
- 日程数据: `project/data/schedule.yaml`(独立于 SQLite; gitignore), 模板 `data/schedule.example.yaml` 随仓库分发; 纯逻辑在 `shuimu/core/schedule.py`, 页签在 `shuimu/gui/schedule_view.py`.
- 索引只存引用, 不复制文件本体; 文件失效保留记录, 由 UI 标灰.
- 测试伪资料: 课程根目录 `content/`(仓库外), 单元测试一律用 tmp_path 保持隔离.
