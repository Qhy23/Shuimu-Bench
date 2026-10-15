"""目录整理: 按文件类型归类到子目录, 预览后再执行.

只做顶层普通文件, 不递归, 不动隐藏文件. 归类是纯函数, 便于测试.
"""

import os
import shutil
from dataclasses import dataclass

CATEGORY_MAP = {
    "文档": {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".txt", ".md",
             ".xls", ".xlsx", ".csv"},
    "图片": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"},
    "音视频": {".mp3", ".wav", ".mp4", ".mkv", ".mov", ".avi", ".flv"},
    "压缩包": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "代码": {".py", ".c", ".cpp", ".h", ".java", ".js", ".ts",
             ".html", ".css"},
}
FALLBACK_CATEGORY = "其他"


@dataclass
class MoveItem:
    """一次计划内的移动."""

    src: str
    dst: str


def category_of(filename: str) -> str:
    """按扩展名判断归类, 无法识别归入"其他"."""
    ext = os.path.splitext(filename)[1].lower()
    for category, exts in CATEGORY_MAP.items():
        if ext in exts:
            return category
    return FALLBACK_CATEGORY


def _dedupe(dst: str, taken: set) -> str:
    """目标已存在(磁盘上或本计划内)时, 追加 _1 _2 后缀."""
    if dst not in taken and not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        candidate = f"{base}_{i}{ext}"
        if candidate not in taken and not os.path.exists(candidate):
            return candidate
        i += 1


def build_plan(directory: str) -> list:
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
        dst = os.path.join(directory, category_of(name), name)
        dst = _dedupe(dst, taken)
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
