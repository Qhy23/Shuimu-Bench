# 水木工作台 ShuimuBench

本地优先的清华学习科研工作台: 以文件资源管理器为基底, 按课程组织资源, 常用操作以插件形式在资源上直接执行并全程留痕. 清华大学《AI 编程进阶: 方法与实践》(00240431) 课程项目.

## 当前状态

- [x] 三栏主窗口(课程空间树 / 文件浏览 / 日志,整理,日程面板)
- [x] 课程工作区(SQLite 索引: 文件/文件夹引用 + 网页链接, 失效标灰, 变更提示)
- [x] 操作留痕(operations 表, 日志页实时展示)
- [x] 目录整理(按类型归类, 预览后执行, 冲突自动加后缀)
- [x] 本地日程面板(周历课表/事件 + 待办, YAML 数据, 外部编辑自动重载)
- [ ] 插件框架与内置插件(作业提交自查, 文档批处理)

## 项目结构

```
main.py              # 入口
shuimu/
  storage.py         # SQLite 存储: 课程/资源索引/操作留痕
  organize.py        # 目录整理: 归类计划与执行(纯逻辑)
  schedule.py        # 日程纯逻辑: YAML 读写/周计算/待办排序
  app.py             # 三栏主窗口 UI
  schedule_view.py   # 日程页签: 周历与待办视图
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

## 日程面板

右栏"日程"页签: 上半周历(每周课表 + 一次性事件, 今天列高亮, 可翻周), 下半待办列表(GUI 新建/勾选完成, 逾期红色, 完成划线置底).

数据文件 `data/schedule.yaml`(首次运行自动从 `data/schedule.example.yaml` 播种):

- **课表导入**: 信息门户"个人课表"导出的 .xls 可经"导入课表..."按钮一键导入(替换课表段, 事件与待办不动); 6 大节映射标准时段, 支持全周/前后八周/X-Y周/第N周, 单双周降级为每周并提示
- 课表(timetable)与事件(events)在任意外部编辑器修改, 保存后 GUI 自动重载
- 待办(todos)可在 GUI 新建与勾选, 也可外部编辑; GUI 保存会全量重写文件, 手写注释会丢失
- 课表条目可选 `weeks: [起, 止]` 学期周次, 需在 config.yaml 配置 `schedule.semester_start`(学期第 1 周的周一)后生效
- 已知限制: 并发编辑无锁, 以最后写入为准; 依赖 xlrd(仅解析 .xls, 不支持 .xlsx)

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
