"""应用配置: config.yaml + 内置默认值.

加载顺序: 显式路径 > 用户数据目录 config.yaml > 项目根 config.yaml > 内置默认值.
配置只读, 编辑 yaml 后重启应用生效. 数据归 SQLite, 配置归文本文件.
"""

import copy
import os

import yaml

from .paths import project_root, user_data_dir

DEFAULTS = {
    "app": {
        "title": "水木工作台 ShuimuBench",
        "window_size": [1200, 720],
        "splitter_sizes": [240, 520, 400],
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
}


def load_config(path: str = None) -> dict:
    """加载配置并合并到默认值上, 返回 dict. 文件缺失或损坏时用默认值."""
    cfg = copy.deepcopy(DEFAULTS)
    for candidate in _candidates(path):
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                user = yaml.safe_load(f)
        except (OSError, yaml.YAMLError):
            break  # 文件损坏也回退默认值
        if isinstance(user, dict):
            _merge(cfg, user)
        break
    return cfg


def organize_rules(cfg: dict) -> dict:
    """从完整配置提取整理规则(categories + fallback)."""
    org = cfg["organize"]
    return {"categories": org["categories"], "fallback": org["fallback_category"]}


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
