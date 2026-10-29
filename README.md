# 水木工作台 ShuimuBench

本地优先的清华学习科研工作台: 以文件资源管理器为基底, 按课程组织资源, 常用操作以插件形式在资源上直接执行并全程留痕. 清华大学《AI 编程进阶: 方法与实践》(00240431) 课程项目.

## 当前状态

- [x] 三栏主窗口(课程空间树 / 文件浏览 / 日志,整理,日程面板)
- [x] 课程工作区(SQLite 索引: 文件/文件夹引用 + 网页链接, 失效标灰, 变更提示)
- [x] 操作留痕(operations 表, 日志页实时展示)
- [x] 目录整理(按类型归类, 预览后执行, 冲突自动加后缀)
- [x] 跨课程资源搜索(CLI search 子命令, 子串大小写不敏感)
- [x] 本地日程面板(周历课表/事件 + 待办, YAML 数据, 外部编辑自动重载)
- [ ] 插件框架与内置插件(作业提交自查, 文档批处理)

## 项目结构

```
main.py                # GUI 入口
cli.py                 # CLI 入口(shim)
shuimu/
  core/                # 后端纯逻辑, 不依赖 Qt
    paths.py           # 路径分发(dev/frozen)
    config.py          # 配置加载与默认值(含字号常量)
    storage.py         # SQLite 存储: 课程/资源索引/操作留痕
    organize.py        # 目录整理: 归类计划与执行
    schedule.py        # 日程纯逻辑: YAML 读写/周计算/待办排序
    schedule_import.py # 个人校历 .html 解析
    controller.py      # 业务动作层(GUI 与 CLI 共用)
  gui/                 # PySide6 界面
    theme.py           # 主题 token 与全局 QSS
    artwork.py         # 自绘 logo 与图标
    titlebar.py        # 无边框标题栏
    week_grid.py       # 自绘周时间网格
    schedule_view.py   # 日程页签: 周历与待办视图
    main_window.py     # 三栏主窗口
  cli/
    main.py            # argparse 子命令 + REPL
tests/                 # 镜像分组: core/ gui/ cli/
```

依赖方向: gui -> core, cli -> core, 单向; core 仅依赖标准库与 yaml.

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py        # GUI
python cli.py --help  # CLI(与 GUI 共用同一数据库, 供无头环境与 AI 工具测试)
```

CLI 与 GUI 共用同一 Controller 业务层, 只是外壳不同. 两种用法: `python cli.py <子命令>` 单次执行; 不带子命令 `python cli.py` 进入交互模式(一次启动逐行输入, help 查看命令, exit 退出).

工作区: `add-course 高等数学` / `add 1 /path/file.pdf` / `resources 1` / `organize <目录> [--yes]` / `log`

日程(与 GUI 同源): `schedule [--week N | --date D]` 列周历(输出带 #N 序号) / `import-schedule 个人校历.html` / `add-todo 标题 [--due D] [--course 课]` / `toggle-todo 1` / `add-event 标题 --date D --start --end` 或 `--weekday 1-7 --session 1-6 --weeks 1-8,11`(按周数展开为系列) / `edit-event #N [--series]` / `rm-event #N [--series]`.

## 日程面板

右栏"日程"页签: 上半周历(每周课表 + 一次性事件, 今天列高亮, 可翻周), 下半待办列表(GUI 新建/勾选完成, 逾期红色, 完成划线置底).

数据文件 `data/schedule.yaml`(首次运行自动从 `data/schedule.example.yaml` 播种), 三段: `courses`(导入课程) / `manual`(手动事件, 预留) / `todos`(待办):

- **课程导入**("导入课程..."按钮, 替换 courses 段, 手动事件与待办不动): 信息门户"个人校历"页设整学期日期后另存页面源码为 **.html** 导入; 逐次事件含确切课时与调休/调课, 同日同名且间隔不超过 15 分钟的联排课自动合并, 编码自动兼容 utf-8/gb18030
- **手动日程**: "添加日程..."支持双模式(按周数与节次 / 按日期和时间); 周数模式按所选周次展开为系列(共享系列号); 周历卡片**右键**可修改/删除(仅本次或整个系列)
- yaml 在任意外部编辑器修改, 保存后 GUI 自动重载
- 待办(todos)可在 GUI 新建与勾选, 也可外部编辑; GUI 保存会全量重写文件, 手写注释会丢失
- config.yaml 配置 `schedule.semester_start`(学期第 1 周的周一)与 `schedule.week_count`(总周数, 课程+考试)后, 导航栏显示学期周次, 学期外显示"假期", 翻周限制在学期内(week_count 此处手动维护, 惯例同 thu-info-app: 秋季 16 课+2 考+5 寒假共 23 周, 春季 16+2 共 18 周, 暑期各年不同)
- 已知限制: 并发编辑无锁, 以最后写入为准; 对 courses 段的修改会在下次导入时被覆盖

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
