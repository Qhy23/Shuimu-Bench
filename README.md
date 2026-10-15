# 水木工作台 ShuimuBench

本地优先的清华学习科研工作台:以文件资源管理器为基底,按课程组织资源,常用操作以插件形式在资源上直接执行并全程留痕。清华大学《AI 编程进阶:方法与实践》(00240431)课程项目。

## 当前状态

- [x] 三栏主窗口骨架(课程空间树 / 文件浏览 / 结果·日志面板)
- [ ] 课程工作区(SQLite 索引:文件/文件夹引用 + 网页链接)
- [ ] 本地日程面板(用户可编辑配置)
- [ ] 操作记录与留痕
- [ ] 插件框架与内置插件(作业提交自查、文档批处理)

## 运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

开发环境:Linux/WSL2;Windows 打包后续处理。

## 测试

```bash
QT_QPA_PLATFORM=offscreen pytest
```
