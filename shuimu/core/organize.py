"""目录整理: 按文件类型归类到子目录, 预览后再执行.

只做顶层普通文件, 不递归, 不动隐藏文件. 归类规则由调用方以
rules={"categories": {...}, "fallback": ...} 传入(默认取 config.DEFAULTS).
"""

import os
import shutil
from dataclasses import dataclass

from shuimu.core.config import DEFAULTS
from shuimu.core.fileops import dedupe_path

_DEFAULT_RULES = {
    "categories": DEFAULTS["organize"]["categories"],
    "fallback": DEFAULTS["organize"]["fallback_category"],
}


@dataclass
class MoveItem:
    """一次计划内的移动."""

    src: str
    dst: str


def category_of(filename: str, rules: dict = None) -> str:
    """按扩展名判断归类, 无法识别归入 fallback."""
    rules = rules or _DEFAULT_RULES
    ext = os.path.splitext(filename)[1].lower()
    for category, exts in rules["categories"].items():
        if ext in exts:
            return category
    return rules["fallback"]


def build_plan(directory: str, rules: dict = None) -> list:
    """生成归类计划: 顶层普通文件按类型移动到 category 子目录.

    返回按文件名排序的 MoveItem 列表; 目录不存在时抛 NotADirectoryError.
    """
    if not os.path.isdir(directory):
        raise NotADirectoryError(directory)
    items = []
    taken = set()
    for name in sorted(os.listdir(directory)):
        src = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isfile(src):
            continue
        dst = os.path.join(directory, category_of(name, rules), name)
        dst = dedupe_path(dst, taken)
        taken.add(dst)
        items.append(MoveItem(src=src, dst=dst))
    return items


def execute_plan(items: list) -> tuple:
    """执行移动计划. 返回 (成功数, 错误信息列表). 单项失败不中断整体."""
    moved, errors = 0, []
    for item in items:
        try:
            os.makedirs(os.path.dirname(item.dst), exist_ok=True)
            shutil.move(item.src, item.dst)
            moved += 1
        except OSError as exc:
            errors.append(f"{item.src}: {exc}")
    return moved, errors
