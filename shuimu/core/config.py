"""应用配置: config.yaml + 内置默认值.

加载顺序(低 -> 高): 内置默认值 <- 项目根 config.yaml <- 用户数据目录
config.yaml, 逐层合并, 高优先级只覆盖显式给出的键(部分文件不会遮蔽
低层的其余配置); 传入显式路径时只读该文件.
配置只读, 编辑 yaml 后重启应用生效. 数据归 SQLite, 配置归文本文件.
"""

import copy
import os
from datetime import date

import yaml

from shuimu.core.paths import project_root, user_data_dir

# 界面字号基准与边界(px): 纯常量与夹取逻辑, GUI 主题自此取用
DEFAULT_FONT_PX = 13
MIN_FONT_PX = 9
MAX_FONT_PX = 20

DEFAULTS = {
    "app": {
        "title": "水木工作台 ShuimuBench",
        "window_size": [1200, 720],
        "splitter_sizes": [240, 520, 400],
        "font_size": DEFAULT_FONT_PX,  # 界面字号(px), 9-20; 运行时 Ctrl+=/- 可调
    },
    "course": {
        "default_name": "未分组",
    },
    "organize": {
        "fallback_category": "其他",
        "categories": {
            "文档": [".pdf", ".doc", ".docx", ".ppt", ".pptx",
                    ".txt", ".md", ".xls", ".xlsx", ".csv"],
            "图片": [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"],
            "音视频": [".mp3", ".wav", ".mp4", ".mkv", ".mov", ".avi", ".flv"],
            "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz"],
            "代码": [".py", ".c", ".cpp", ".h", ".java", ".js", ".ts",
                    ".html", ".css"],
        },
    },
    "schedule": {
        "semester_start": "",  # 学期第 1 周的周一(YYYY-MM-DD), 空为不启用周次显示
        "week_count": 0,       # 学期总周数(课程+考试), 0 为不限制
    },
    "fileops": {
        "trash_dir": "",  # 文件页删除的回收站目录; 空用系统回收站(见 core/trash.py)
    },
}


def load_config(path: str = None) -> dict:
    """加载配置并逐层合并, 返回 dict.

    缺失的候选跳过, 损坏的候选跳过(已合并的更低层保留).
    """
    cfg = copy.deepcopy(DEFAULTS)
    for candidate in reversed(list(_candidates(path))):
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                user = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            continue  # 损坏文件跳过, 不影响其他层
        if isinstance(user, dict):
            _merge(cfg, user)
    return cfg


def organize_rules(cfg: dict) -> dict:
    """从完整配置提取整理规则(categories + fallback)."""
    org = cfg["organize"]
    return {"categories": org["categories"], "fallback": org["fallback_category"]}


def semester_start_date(cfg: dict):
    """提取并解析学期起点(建议填第 1 周的周一).

    空串/缺失返回 None(不启用周次过滤); 格式非法同样返回 None,
    静默回退不中断启动, 与 load_config 对损坏文件的容错一致.
    YAML 未加引号的日期会被解析成 date 对象, 一并接受.
    """
    raw = (cfg.get("schedule") or {}).get("semester_start", "")
    if isinstance(raw, date):
        return raw
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def clamp_font_px(value) -> int:
    """字号夹取到 [MIN_FONT_PX, MAX_FONT_PX]; 非法值回退默认."""
    try:
        px = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FONT_PX
    return max(MIN_FONT_PX, min(MAX_FONT_PX, px))


def font_size_value(cfg: dict) -> int:
    """提取界面字号并夹取到 [9, 20]; 缺省 13, 非法回退默认."""
    raw = (cfg.get("app") or {}).get("font_size", DEFAULT_FONT_PX)
    return clamp_font_px(raw)


def semester_week_count(cfg: dict) -> int:
    """提取学期总周数(课程+考试周).

    未配置/非法/非正数返回 0(不限制翻周范围).
    """
    raw = (cfg.get("schedule") or {}).get("week_count", 0)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def trash_dir_value(cfg: dict) -> str:
    """提取回收站目录配置(原始串, 展开/解析由 core/trash.py 负责).

    空串/缺失/非字符串返回 ""(用系统回收站).
    """
    raw = (cfg.get("fileops") or {}).get("trash_dir", "")
    return raw if isinstance(raw, str) else ""


def _candidates(path):
    """配置文件候选位置, 按优先级排列."""
    if path is not None:
        yield path
        return
    yield os.path.join(user_data_dir(), "config.yaml")
    yield os.path.join(project_root(), "config.yaml")


def _merge(base: dict, override: dict) -> None:
    """递归合并 override 进 base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value)
        else:
            base[key] = value
