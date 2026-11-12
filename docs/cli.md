# CLI 使用说明

CLI 与图形界面共用同一数据库和业务逻辑, 适合无头环境, 脚本与外部工具调用. 本文覆盖**核心功能**(不含插件; 插件见各自文档).

## 启动方式

```bash
python cli.py <子命令> [参数]    # 单次执行
python cli.py                    # 交互 REPL: 逐行输入命令, help 查看, exit 退出
```

REPL 由 readline 提供上下历史与 Tab 补全; 两种方式参数完全一致.

全局选项(须在子命令之前):

| 选项 | 说明 |
|---|---|
| `--db PATH` | 数据库路径, 默认 `data/shuimu.db`, 仅启动时生效 |
| `--schedule PATH` | 日程 YAML 路径, 默认 `data/schedule.yaml` |
| `--links PATH` | 常用链接 YAML 路径, 默认 `data/links.yaml` |

输出约定: 成功走 stdout, 失败与错误提示走 stderr; 退出码 0 成功, 2 失败(便于脚本判断).

## 课程与资源索引

索引只存引用: 登记不移动不复制文件; 删除索引条目不动磁盘. 文件被外部移动/删除后条目保留并标"失效", 内容变化标"已变更".

```bash
python cli.py courses                    # 列出全部课程
python cli.py add-course 高等数学         # 新建课程
python cli.py del-course 2               # 删除课程及其索引(不动磁盘文件)
python cli.py add 1 /path/to/file.pdf    # 登记文件/目录/链接(http(s) 开头自动识别为链接)
python cli.py resources 1                # 列出课程资源与状态(失效/已变更/ok)
python cli.py remove 12                  # 按 id 移除一条索引
python cli.py search 关键词              # 跨课程搜索(子串, 大小写不敏感, 按课程分组)
```

同一文件可以登记进多门课; `add` 的第一个参数是课程 id(`courses` 输出的 `#N`).

## 目录整理

按 `config.yaml` 的 `organize.categories`(类别 -> 扩展名列表)归类:

```bash
python cli.py organize ./downloads        # 只预览: 显示每个文件的移动计划
python cli.py organize ./downloads --yes  # 执行; 目标同名自动加 _1 后缀
```

整理计划自动跳过程序自身的数据库文件.

## 操作留痕

```bash
python cli.py log        # 最近 200 条操作: 动作/参数/路径快照/时间/状态
```

留痕覆盖索引增删, 整理, 文件页操作, 插件运行与校园/学堂动作(不含密码与验证码).

## 插件命令

通用插件框架入口(各插件的具体用法见其文档):

```bash
python cli.py plugins                        # 插件清单: 来源/状态/权限/适用类型
python cli.py plugin-enable campus --yes     # 启用(--yes 跳过 network 授权确认)
python cli.py plugin-disable campus          # 禁用(禁用即不加载其代码)
python cli.py plugin-run <id> --path <目标> --set K=V   # 运行并留痕
```

## 常用链接

链接保存在 `data/links.yaml`, 打开走系统浏览器(WSL 下自动优先 wslview):

```bash
python cli.py links                                        # 按分组列出
python cli.py add-link 网络学堂 https://learn.tsinghua.edu.cn --group 课堂资源
python cli.py edit-link 网络学堂 --url https://new.example   # 只改给定的字段
python cli.py rm-link 网络学堂
python cli.py open-link 网络学堂
```

`--group` 可选, 缺省归入"未分组"; 分组名不持久化, 由条目顺序派生.

## 日程

数据在 `data/schedule.yaml`, 三段: `courses`(校历导入) / `manual`(手动事件) / `todos`(待办). 外部编辑保存后 GUI 自动重载; GUI 保存会全量重写文件, 手写注释会丢失.

```bash
python cli.py schedule                  # 本周周历(带 #N 全局序号)
python cli.py schedule --week 6         # 指定周
python cli.py schedule --date 2026-11-20
python cli.py todos                     # 待办(未完成在前, 逾期红色)
python cli.py add-todo 微积分作业 --due 2026-10-26 --course 高等数学
python cli.py toggle-todo 3             # 按 id 翻转完成状态
```

**导入个人校历**: 信息门户"个人校历"页设置整学期日期范围后, 把页面另存为 .html 再导入. 逐次事件含确切课时与调休调课; 同日同名且间隔不超过 15 分钟的联排课自动合并; 编码自动兼容 utf-8/gb18030. 导入只替换 courses 段, 手动事件与待办不动.

```bash
python cli.py import-schedule 个人校历.html
```

**手动事件**两种模式:

```bash
# 按日期和时间(一次性)
python cli.py add-event 答疑 --date 2026-11-20 --start 14:00 --end 16:00 --location 系馆

# 按周数与节次(展开为系列, 共享系列号)
python cli.py add-event 实验课 --weekday 3 --session 2 --weeks 1-8,11
```

`--session` 为大节 1-6(按标准时段填充起止), 也可用 `--start/--end HH:MM` 自定义. 修改与删除按 `schedule` 输出的 `#N` 序号, 系列事件可仅本次或整个系列(`--series`):

```bash
python cli.py edit-event #5 --start 15:00
python cli.py rm-event #5 --series
```

## 配置文件

项目根 `config.yaml`, 全部字段可选(注释即说明), 主要段落:

| 段 | 字段 | 说明 |
|---|---|---|
| app | window_size / splitter_sizes / font_size | 界面尺寸与字号(界面内 Ctrl+=/-/0 可调) |
| organize | categories / fallback_category | 整理规则: 类别 -> 扩展名 |
| schedule | semester_start / week_count | 学期第 1 周的周一(YYYY-MM-DD)与总周数; 填写后界面显示学期周次 |
| fileops | trash_dir | 文件页删除的回收站目录; 留空用系统回收站(WSL 建议自建目录) |
| learn | download_dir | 学堂下载根目录; 留空用 data/learn |

用户数据目录(~/.local/share/shuimu/config.yaml)的同名配置优先级更高, 适合放本机专属路径.
