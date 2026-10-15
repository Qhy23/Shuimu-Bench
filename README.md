# 水木工作台 ShuimuBench

本地优先的清华学习科研工作台: 以文件资源管理器为基底, 按课程组织资源, 常用操作以插件形式在资源上直接执行并全程留痕. 清华大学《AI 编程进阶: 方法与实践》(00240431) 课程项目.

## 当前状态

- [x] 三栏主窗口(课程空间树 / 文件浏览 / 日志,整理面板)
- [x] 课程工作区(SQLite 索引: 文件/文件夹引用 + 网页链接, 失效标灰, 变更提示)
- [x] 操作留痕(operations 表, 日志页实时展示)
- [x] 目录整理(按类型归类, 预览后执行, 冲突自动加后缀)
- [ ] 本地日程面板(用户可编辑配置)
- [ ] 插件框架与内置插件(作业提交自查, 文档批处理)

## 项目结构

```
main.py              # 入口
shuimu/
  storage.py         # SQLite 存储: 课程/资源索引/操作留痕
  organize.py        # 目录整理: 归类计划与执行(纯逻辑)
  app.py             # 三栏主窗口 UI
tests/               # pytest 单元测试 + GUI 冒烟测试
```

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py        # GUI
python cli.py --help  # CLI(与 GUI 共用同一数据库, 供无头环境与 AI 工具测试)
```

CLI 两种用法: `python cli.py <子命令>` 单次执行; 不带子命令 `python cli.py` 进入交互模式(一次启动逐行输入, help 查看命令, exit 退出). 子命令示例: `add-course 高等数学` / `add 1 /path/file.pdf` / `organize <目录> [--yes]` / `log`.

开发环境: Linux/WSL2; Windows 打包后续处理.

测试用伪资料在课程根目录 `content/`(课程目录树 + 待整理散乱目录).

## 测试

```bash
QT_QPA_PLATFORM=offscreen pytest
```

## 开发约定

- commit message 遵循 Conventional Commits 1.0.0, description 使用中文.
- 代码, Markdown, commit message 中统一使用半角标点.
- lint: `pylint --rcfile=../.pylintrc main.py`(配置在课程根目录 `.pylintrc`).
