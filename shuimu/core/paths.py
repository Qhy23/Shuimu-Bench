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


def builtin_plugins_dir() -> str:
    """内置插件目录(shuimu/plugins, 每个子目录一个插件).

    frozen 时经 PyInstaller datas 落地为 _MEIPASS/shuimu_plugins;
    加载统一按文件路径读, dev/frozen 代码路径一致.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", "")
        return os.path.join(base, "shuimu_plugins")
    return os.path.join(project_root(), "shuimu", "plugins")


def external_plugins_dir() -> str:
    """用户外部插件目录(dev 模式也在用户数据目录, 个人扩展不入仓库).

    不存在视为空(发现层容错).
    """
    return os.path.join(user_data_dir(), "plugins")


def default_plugins_state_path() -> str:
    """插件启用状态 YAML 位置. 与 default_links_path 同款分叉:
    dev 在项目 data/, frozen 在用户数据目录."""
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "plugins.yaml")
    return os.path.join(project_root(), "data", "plugins.yaml")


def default_campus_session_path() -> str:
    """校园登录会话 JSON 位置(cookies+本人信息缓存; 密码永不落盘).
    与 default_links_path 同款分叉: dev 在项目 data/, frozen 在用户数据目录."""
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "campus-session.json")
    return os.path.join(project_root(), "data", "campus-session.json")


def default_campus_fingerprint_path() -> str:
    """校园登录设备指纹位置(独立于会话, 登出保留以维持信任设备)."""
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "campus-fingerprint")
    return os.path.join(project_root(), "data", "campus-fingerprint")


def default_learn_download_dir() -> str:
    """学堂下载默认目录(课件/附件按课程名建子目录). 同款 dev/frozen 分叉."""
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "learn")
    return os.path.join(project_root(), "data", "learn")


def default_ai_config_path() -> str:
    """AI 工具配置 JSON 位置(base_url/model/api_key; 0o600, 不入仓库).

    与 default_campus_session_path 同款分叉: dev 在项目 data/,
    frozen 在用户数据目录.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(user_data_dir(), "ai-config.json")
    return os.path.join(project_root(), "data", "ai-config.json")


def user_data_dir() -> str:
    """平台用户数据目录."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ShuimuBench")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "shuimu")


def user_themes_dir() -> str:
    """用户自定义主题目录(YAML 主题文件放此, 加载见 gui/theme.py)."""
    return os.path.join(user_data_dir(), "themes")


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
