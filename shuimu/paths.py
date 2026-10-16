"""路径与平台相关的纯函数. 不依赖 Qt, GUI/CLI/QML 共用."""

import os
import sys


def project_root() -> str:
    """项目根目录(本包的上一级)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_db_path() -> str:
    """默认数据库位置.

    开发模式(源码运行)放在项目内 data/shuimu.db; 打包后(frozen)按平台
    规范放用户数据目录: Windows %APPDATA%, Linux ~/.local/share.
    """
    if getattr(sys, "frozen", False):
        base = user_data_dir()
        return os.path.join(base, "shuimu.db")
    return os.path.join(project_root(), "data", "shuimu.db")


def user_data_dir() -> str:
    """平台用户数据目录."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ShuimuBench")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "shuimu")


def default_workspace_root() -> str:
    """中栏文件浏览器的默认根目录.

    优先用课程根目录的 content/(测试伪资料), 不存在则退回家目录.
    """
    content = os.path.join(os.path.dirname(project_root()), "content")
    if os.path.isdir(content):
        return content
    return os.path.expanduser("~")
