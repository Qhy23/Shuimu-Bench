# 水木工作台 ShuimuBench

本地优先的清华学习科研工作台: 以文件资源管理器为基底, 按课程组织资源, 常用操作以插件形式在资源上直接执行并全程留痕.

面向清华大学在读学生, 尤其是每学期在"网络学堂网页 + 本地散乱文件夹 + 各种零散小工具"之间切换的人: 下载的课件不再失去课程语义, 对文件做过的操作都有据可查, 常用检查一键完成.

## 功能特性

**本地核心(零网络, 零账号)**

- 课程工作区: 文件/文件夹以引用方式登记(不移动不复制原文件), 网页链接同样入库; 失效资源标灰保留
- 三栏界面: 左课程空间树 / 中栏文件浏览, 常用链接与网络学堂 / 右栏目录整理, 日程与校园面板
- 文件页资源管理器操作: 复制/剪切/粘贴/删除入回收站/重命名/新建文件夹, 全程留痕, Ctrl+Z 撤销
- 目录整理: 按类型生成归类计划, 预览确认后执行
- 本地日程: 周历课表 + 待办; 支持导入信息门户"个人校历"页面; 数据为 YAML, 外部编辑自动重载
- 跨课程资源搜索与操作留痕(每次操作的输入/参数/时间/状态)

**内置插件(可选, 默认禁用)**

- [校园账户登录 campus](docs/plugin-campus.md): 经 WebVPN 与统一认证登录, 支持二次认证与信任设备
- [网络学堂 learn](docs/plugin-learn.md): 复用校园会话拉取课程/作业/课件/公告, 选择性下载并自动归档进课程, 未交作业自动同步进日程待办
- [清华云盘 cloud](docs/plugin-cloud.md): 粘贴分享链接(免登录)浏览多课程资料库, 同名目录一键挂载为课程链接资源
- [AI 工具 ai](docs/plugin-ai.md): OpenAI 兼容服务问答(智谱/DeepSeek/Kimi/Ollama 等), 勾选课程资料与学堂缓存作上下文, 流式输出

涉及联网的功能全部以插件形式提供, 启用时需明确授权; 核心功能离线完全可用.

## 安装与运行

需要 Python 3.12+:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python main.py        # 图形界面
python cli.py --help  # 命令行(与 GUI 共用同一数据库)
```

CLI 完整用法见 [docs/cli.md](docs/cli.md); 两种形态: `python cli.py <子命令>` 单次执行, 不带子命令进入交互 REPL(支持历史与 Tab 补全).

不想装 Python 可用打包版本: 从 GitHub Releases 下载对应平台压缩包, 解压后运行目录内 `ShuimuBench`(图形界面) 或 `shuimu-cli`(命令行), 无需安装依赖. 打包版数据保存在用户数据目录(Windows `%APPDATA%\ShuimuBench`, Linux `~/.local/share/shuimu/`), 不在程序目录. 源码自行打包: `pyinstaller --noconfirm shuimu.spec`, 详见 [docs/packaging.md](docs/packaging.md).

## 数据与隐私

- 所有数据保存在本机: 索引与留痕在 `data/shuimu.db`(SQLite), 日程在 `data/schedule.yaml`, 常用链接在 `data/links.yaml`
- 校园登录的密码与验证码**永不落盘**, 只保存会话 cookie(权限 0600)与设备指纹; 详见 [docs/plugin-campus.md](docs/plugin-campus.md)
- 索引只存引用路径, 删除索引条目不影响磁盘文件
- 配置见项目根 `config.yaml`, 各字段有注释; 本机专属配置可放在用户数据目录(~/.local/share/shuimu/config.yaml), 优先级更高

## 文档

- [CLI 使用说明](docs/cli.md): 全部核心子命令, REPL, 配置与退出码
- [界面主题](docs/theme.md): 亮/暗主题切换与自定义主题 YAML
- [插件: 校园账户登录](docs/plugin-campus.md)
- [插件: 网络学堂](docs/plugin-learn.md)
- [插件: 清华云盘](docs/plugin-cloud.md)
- [插件: AI 工具](docs/plugin-ai.md)
- [打包与发布](docs/packaging.md)
