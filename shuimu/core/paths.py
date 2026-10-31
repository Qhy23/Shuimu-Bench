"""路径与平台相关的纯函数. 不依赖 Qt, GUI/CLI/QML 共用."""

import os
import sys


def project_root() -> str:
    """项目根目录(本模块位于 shuimu/core/ 下, 上溯三级)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def default_db_path() -> str:
    """默认数据库位置.

    开发模式(源码运行)放在项目内 data/shuimu.db; 打包后(frozen)按平台
    规范放用户数据目录: Windows %APPDATA%, Linux ~/.local/share.
    """
    if getattr(sys, "frozen", False):
        base = user_data_dir()
        return os.path.join(base, "shuimu.db")
    return os.path.join(project_root(), "data", "shuimu.db")


def default_schedule_path() -> str:
    """日程 YAML 位置. 与 default_db_path 同款分叉: dev 在项目 data/,
    frozen 在用户数据目录."""
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "schedule.yaml")
    return os.path.join(project_root(), "data", "schedule.yaml")


def template_schedule_path() -> str:
    """随仓库/包分发的日程模板位置(首次运行复制为数据文件)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        return os.path.join(base, "data", "schedule.example.yaml")
    return os.path.join(project_root(), "data", "schedule.example.yaml")


def default_links_path() -> str:
    """常用链接 YAML 位置. 与 default_db_path 同款分叉: dev 在项目 data/,
    frozen 在用户数据目录."""
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "links.yaml")
    return os.path.join(project_root(), "data", "links.yaml")


def template_links_path() -> str:
    """随仓库/包分发的常用链接模板位置(首次运行复制为数据文件)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        return os.path.join(base, "data", "links.example.yaml")
    return os.path.join(project_root(), "data", "links.example.yaml")


def user_data_dir() -> str:
    """平台用户数据目录."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ShuimuBench")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "shuimu")


def cli_history_path() -> str:
    """REPL 命令历史文件(用户级, 跨会话持久; dev 模式也不入仓库)."""
    return os.path.join(user_data_dir(), "cli_history")


def default_workspace_root() -> str:
    """中栏文件浏览器的默认根目录.

    优先用课程根目录的 content/(测试伪资料), 不存在则退回家目录.
    """
    content = os.path.join(os.path.dirname(project_root()), "content")
    if os.path.isdir(content):
        return content
    return os.path.expanduser("~")
