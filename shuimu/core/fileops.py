"""文件操作纯逻辑: 复制/移动/重命名/新建文件夹, 冲突自动改名, 危险操作防护.

不依赖 Qt, 供 controller(GUI/CLI)共用. 批操作单条失败不中断,
结果逐条收集为 FileOpItem, 由调用方留痕与提示.
"""

import os
import shutil
from dataclasses import dataclass

DEFAULT_DIR_NAME = "新建文件夹"


@dataclass
class FileOpItem:
    """批操作中单个条目的结果."""

    src: str
    dst: str           # 实际落位(可能经去重改名); 失败时为预期目标
    status: str        # "ok" | "skipped" | "failed"
    error: str = ""


def dedupe_path(dst: str, taken: set = None) -> str:
    """目标已存在(磁盘上或本计划内 taken 集合)时, 追加 _1 _2 后缀."""
    taken = taken or set()
    if dst not in taken and not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        candidate = f"{base}_{i}{ext}"
        if candidate not in taken and not os.path.exists(candidate):
            return candidate
        i += 1


def is_within(child: str, ancestor: str) -> bool:
    """child 是否位于 ancestor 内部(含相等). 跨盘(或非法组合)视为不在内部."""
    child = os.path.realpath(child)
    ancestor = os.path.realpath(ancestor)
    try:
        return os.path.commonpath([child, ancestor]) == ancestor
    except ValueError:
        return False


def check_target(src: str, dst_dir: str):
    """复制/移动的单条防护. 返回 None 表示允许, 否则返回错误文案."""
    if not os.path.exists(src):
        return "源不存在(可能已被移动或删除)"
    if not os.path.isdir(dst_dir):
        return "目标目录不存在"
    if os.path.isdir(src) and is_within(dst_dir, src):
        return "不能把文件夹放进自身内部"
    return None


def copy_batch(sources: list, dst_dir: str) -> list:
    """把一批文件/目录复制进 dst_dir, 冲突自动加后缀.

    文件用 copy2(保 mtime), 目录用 copytree; 同目录复制标记 skipped.
    """
    return _batch(sources, dst_dir, _copy_one)


def move_batch(sources: list, dst_dir: str) -> list:
    """把一批文件/目录移动进 dst_dir(剪切粘贴), 冲突自动加后缀."""
    return _batch(sources, dst_dir, shutil.move)


def rename_entry(path: str, new_name: str) -> str:
    """同目录内改名, 返回新路径. 非法输入抛 ValueError, IO 失败抛 OSError.

    改名不做自动去重: 目标已存在即失败, 必须显式成功或失败.
    """
    name = new_name.strip()
    if not name:
        raise ValueError("名称不能为空")
    if os.sep in name or "/" in name or "\\" in name:
        raise ValueError("名称不能包含路径分隔符")
    if name in (".", ".."):
        raise ValueError("名称不能为 . 或 ..")
    if not os.path.exists(path):
        raise OSError(f"源不存在: {path}")
    if name == os.path.basename(path):
        raise ValueError("名称未变化")
    dst = os.path.join(os.path.dirname(path), name)
    if os.path.exists(dst):
        raise ValueError(f"目标已存在: {name}")
    os.rename(path, dst)
    return dst


def make_dir(parent: str, name: str = None) -> str:
    """在 parent 下新建文件夹, 重名自动加后缀, 返回实际创建路径."""
    if not os.path.isdir(parent):
        raise NotADirectoryError(parent)
    name = (name or "").strip() or DEFAULT_DIR_NAME
    if os.sep in name or "/" in name or "\\" in name:
        raise ValueError("名称不能包含路径分隔符")
    if name in (".", ".."):
        raise ValueError("名称不能为 . 或 ..")
    dst = dedupe_path(os.path.join(parent, name))
    os.mkdir(dst)
    return dst


def _batch(sources: list, dst_dir: str, transfer) -> list:
    """批操作骨架: 防护 -> 去重 -> transfer, 单条失败不中断."""
    items = []
    taken = set()
    for src in sources:
        dst = os.path.join(dst_dir, os.path.basename(src))
        error = check_target(src, dst_dir)
        if error is None and os.path.dirname(
                os.path.realpath(src)) == os.path.realpath(dst_dir):
            items.append(FileOpItem(src, src, "skipped", "已在目标位置"))
            continue
        if error is not None:
            items.append(FileOpItem(src, dst, "failed", error))
            continue
        dst = dedupe_path(dst, taken)
        taken.add(dst)
        try:
            transfer(src, dst)
            items.append(FileOpItem(src, dst, "ok"))
        except OSError as exc:
            items.append(FileOpItem(src, dst, "failed", str(exc)))
    return items


def _copy_one(src: str, dst: str) -> None:
    """复制单条: 文件 copy2 保 mtime, 目录 copytree 递归."""
    if os.path.isdir(src):
        shutil.copytree(src, dst, copy_function=shutil.copy2)
    else:
        shutil.copy2(src, dst)
