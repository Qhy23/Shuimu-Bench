# AGENTS.md -- 水木工作台(ShuimuBench)

## 项目概述

本地优先的清华学习科研工作台. 以文件资源管理器为基底, 按课程组织资源(文件/文件夹引用 + 网页链接), 常用操作以插件形式执行并全程留痕. 清华大学《AI 编程进阶: 方法与实践》课程项目.

## 技术栈与环境

- 语言: Python 3.12
- GUI: PySide6 (Qt), 中栏文件浏览用 QFileSystemModel 直读磁盘
- 存储: SQLite(标准库 sqlite3, WAL 模式), 不用 ORM
- 测试: pytest + pytest-qt, 无头环境设 `QT_QPA_PLATFORM=offscreen`
- 开发环境: Linux/WSL2 (WSLg); 第一版交付 Linux, Windows 打包(PyInstaller, 需 Windows 侧构建)后续处理
- 运行时零网络零模型依赖(核心功能); 校园服务四件自 2026-11-08 经助教确认转为**可选 network 插件**(显式授权联网), 见"范围控制"

## 代码与文档约定(必须遵守)

- **标点**: 代码, Markdown, commit message 中全部使用半角标点(与用户的聊天不受此限).
- **导入**: shuimu 包内统一 `shuimu.` 前缀绝对导入, 不用相对导入; 子包 `__init__.py` 仅暴露必要接口(当前只有 cli 的 main).
- **Commit**: 遵循 Conventional Commits 1.0.0 (https://www.conventionalcommits.org/zh-hans/v1.0.0/), `<description>` 使用中文. 示例: `feat: 新增课程工作区的 SQLite 索引`.
- **lint**: 提交前运行 `.venv/bin/pylint --rcfile=../.pylintrc <changed-files>`; `.pylintrc` 位于课程根目录(项目外一层), PySide6 的 E0611 由其 extension-pkg-allow-list 处理, 不要用行内 disable 绕过.
- **Commit 署名**: AI 参与生成的提交在 message 末尾加 trailer, 如实注明本次实际使用的 AI 工具与模型, 如 `Co-Authored-By: OpenCode (Kimi K3) <opencode-agent[bot]@users.noreply.github.com>`, `Co-Authored-By: Claude Code (GLM-5.3) <noreply@anthropic.com>`.
- **安全**: 不提交 API Key 等凭据; `.env`, `.venv/`, `*.db` 已在 .gitignore.

## 范围控制

- MVP: 三栏主窗口 + 课程工作区(SQLite 索引) + 文件浏览 + 本地日程 + 操作记录.
- 插件框架已落地(2026-11-09, plugin-framework 分支): 运行时在 `shuimu/core/pluginman.py`, 内置插件在 `shuimu/plugins/`(与外部插件同一加载路径, 无特权代码路径); 内置插件: hwcheck(占位待重设计, 见 HANDOFF §5), campus(校园账户登录, 2026-11-11 经真实登录校准), learn(网络学堂, 2026-11-12 经真实登录校准), cloud(清华云盘分享链接挂载, 2026-11-13 经真实分享链接校准).
- **在线接入边界**(2026-11-08 经助教确认放开): 学校服务以可选 network 插件形态提供, 插件默认禁用, 启用时经确认授权联网; 核心功能保持零网络. 校园服务四件: 校园账户登录(已完成) / 网络学堂(已完成) / 清华云盘(已完成) / AI 工具.
- 本学期不做: 插件市场, PDF 阅读器.
- 详细规划见 ../.claude/project-proposal.md.

## 常用命令

```bash
source .venv/bin/activate
python main.py                                # 运行 GUI
python cli.py --help                          # CLI(无头/外部测试用)
QT_QPA_PLATFORM=offscreen pytest              # 测试
pylint --rcfile=../.pylintrc main.py shuimu/ tests/   # lint(配置在课程根目录)
python cli.py plugins                         # 插件清单(含 network 状态)
python cli.py plugin-enable campus --yes      # 启用校园插件(联网授权)
python cli.py campus-login                    # 校园登录(密码交互输入)
python cli.py learn-refresh                   # 学堂全量刷新(需先启用 learn 并校园登录)
python cli.py cloud-mount "https://cloud.tsinghua.edu.cn/d/<分享id>/"   # 挂载云盘分享库(需先启用 cloud)
```

## 数据组织

- 数据库: `project/data/shuimu.db`(SQLite, WAL; 已 gitignore).
- 三表: `courses`(课程), `resources`(索引: kind 为 file/dir/link, 文件类统一存绝对路径 + size/mtime), `operations`(操作留痕, params/detail 为 JSON).
- 日程数据: `project/data/schedule.yaml`(独立于 SQLite; gitignore), 模板 `data/schedule.example.yaml` 随仓库分发; 纯逻辑在 `shuimu/core/schedule.py`, 页签在 `shuimu/gui/schedule_view.py`.
- 常用链接: `project/data/links.yaml`(独立于 SQLite; gitignore), 模板 `data/links.example.yaml` 随仓库分发; 条目可带 `group` 分组(平铺字段, 组名不持久化, 显示时由 `grouped_links` 派生: 首现顺序+未分组末尾); 纯逻辑在 `shuimu/core/quicklinks.py`, 分组树页签在 `shuimu/gui/links_page.py`(中栏"链接"页, 经 `core/url_open.py` 分发层调系统浏览器打开, 为未来浏览器插件预留注册口).
- 插件: 清单/校验/发现/加载在 `shuimu/core/pluginman.py`(manifest YAML: id/权限/applies_to/声明式参数 str|bool|int|choice|path|secret, api_version 版本协商); 契约层 `shuimu/core/pluginapi.py`(PluginJob/PluginFinding/PluginReport + run(job) 签名, 纯数据零 Qt); 内置插件 `shuimu/plugins/`(hwcheck 与 campus), 外部插件放 `~/.local/share/shuimu/plugins/<目录>/`; 启用状态 `data/plugins.yaml`(gitignore; disabled 与 network_enabled 两清单, disabled 优先); 插件动作经 `controller.run_plugin` 统一留痕(action 为 `plugin:<id>`, status 记运行成败, ActionResult.ok 记检查是否通过, 二者分离; **secret 参数值不入留痕**, 只记参数名); applies_to 含 `app` 表示无文件目标动作型(空 targets 放行); 插件动作不入 undo 栈. **权限是声明式门禁而非沙箱**: read 默认放行, write 由宿主先确认, **network 默认禁用, 启用经确认即授权联网**, 入口代码在宿主进程内运行, 外部插件放入即视为信任. **服务型插件的专属代码跟插件包走**: 每个内置服务插件包(campus/learn/cloud)内含 plugin.py(薄壳) + cli.py(专属子命令) + GUI 页签模块(campus_tab.py/learn_page.py/cloud_page.py, 主窗口仅 import 接线); 主 CLI 对三个插件均只做两行接线; GUI 页签由主窗口按启用状态显隐(见"数据组织"下主窗口条目).
- 主窗口: 左栏页签 = 课程(课程空间树 + 操作按钮) / 插件(**插件面板**: 全部插件记录 + 启用/禁用按钮, network 插件启用经确认框); 中栏页签 = 文件/链接/学堂/云盘, 右栏页签 = 整理/日程/校园; **服务型插件页签随启用状态显隐**(setTabVisible, 禁用即隐藏且状态保留, 隐藏当前活动页签先切回首页签). `gui/` 下只放通用组件(file/links/schedule/plugin_runner/dialogs/theme/titlebar/artwork 等), 插件专属页签模块在插件包内.
- 校园登录: 协议在 `shuimu/core/campus.py`(WebVPN+新统一认证两阶段, SM2 加密密码, 2FA 登录与漫游均可能出现, INFO 门户漫游), 传输层 `shuimu/core/campus_net.py`(单跳 Transport 抽象 + urllib 实现 + MemoryTransport 假 transport 离线测试), 国密实现 `shuimu/core/sm2.py`(纯标准库, 国标向量 + gmssl 双向互通 + 真实服务端验证); GUI 入口 = 右栏"校园"页签(`plugins/campus/campus_tab.py` 三态 + 2FA 对话框), CLI 入口 campus-login/status/logout(`plugins/campus/cli.py`, 密码 getpass 交互, 绝不走 argv); controller 动作 campus_login/status/logout 留痕(不含密码/验证码). **凭据安全: 密码与验证码永不落盘不留痕**, 只持久化会话 `data/campus-session.json`(gitignored, 0o600) 与设备指纹 `data/campus-fingerprint`(登出保留); 登录为同步调用(网络慢时 UI 短暂冻结, 线程化留后续). 协议沉 core 是刻意的(11-09/11-12 决策): core 承载可离线测试的协议与编排, 插件包承载 GUI/CLI/壳, 主窗口与主 CLI 只接线.
- 网络学堂: 协议在 `shuimu/core/learn.py`(WebVPN 加密主机段直连复用校园会话, 学堂会话失效自动经 INFO 门户漫游重建, 判定串集中常量区), 编排 `core/learn_sync.py`(差分刷新/课程匹配/下载/待办同步), controller 动作在 `core/learn_actions.py`(LearnActions 混入, ActionResult 在 `core/actions.py`); 缓存为 storage 的 6 张 learn 表(差分保留关联/描述缓存/待办映射), 下载落 `learn.download_dir`(默认 data/learn/<课名>/, 关联课程引用式登记); 未交作业同步日程待办([学堂] 前缀 + DB todo_tid 映射, 手动待办永不碰); GUI = 中栏"学堂"页签(`plugins/learn/learn_page.py` 三态), CLI = learn-* 子命令(`plugins/learn/cli.py`). 用户文档在 `docs/`(README 只留介绍与用法, 开发约定归本文件, 进度归 HANDOFF).
- 清华云盘: 协议在 `shuimu/core/cloud.py`(匿名读分享链接, 目录列举 API + og:title 抓取 + ?p= 子目录链接, 判定串集中常量区), 编排 `core/cloud_sync.py`(目录树收集/课程名三级匹配/引用式登记, 重复挂载因 UNIQUE 转 skipped 幂等), controller 动作在 `core/cloud_actions.py`(CloudActions 混入, 浏览只读不留痕/挂载登记留痕); 免登录免凭据, 不整库下载(只登记 kind=link 资源, display_name=目录名, storage.add_resource 因此带 display_name 可选参); GUI = 中栏"云盘"页签(`plugins/cloud/cloud_page.py` 两态: 未启用引导/主视图, 无登录态), CLI = cloud-* 子命令(`plugins/cloud/cli.py`); 校准探针 `test/workspace/cloud_probe.py`(2026-11-13 实测: dirents 参数名是 path 非 p, 无效 token 404+error_msg).
- 索引只存引用, 不复制文件本体; 文件失效保留记录, 由 UI 标灰.
- 文件页操作(复制/移动/删除/重命名/新建文件夹)经 controller 留痕; 纯逻辑在 `shuimu/core/fileops.py`(find_conflicts 预检/move_back 撤销移动); 粘贴同名冲突逐个弹框决策(覆盖=旧文件先入回收站/跳过/保留两者加 _1, 可"应用于其余全部"); Ctrl+Z 撤销栈在 controller(内存 LIFO, 上限 100, 不跨会话, 无 redo): 复制与新建入回收站, 移动移回原位, 删除从回收站恢复, 重命名改回; 删除移入回收站, 后端在 `shuimu/core/trash.py` — 优先 config.yaml `fileops.trash_dir` 指定目录(WSL 测试用, 布局同 freedesktop: files/ + info/*.trashinfo), 留空用系统回收站(Windows Shell / XDG Trash).
- 测试伪资料: 课程根目录 `content/`(仓库外), 单元测试一律用 tmp_path 保持隔离.
