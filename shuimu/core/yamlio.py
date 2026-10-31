"""YAML 数据文件写入与播种的共用逻辑.

schedule 与 quicklinks 的数据文件同为"用户可编辑的纯 YAML + GUI 全量重写",
保存与播种逻辑在此共用(不依赖 Qt).
"""

import os
import shutil

import yaml


def atomic_write_yaml(path: str, root: dict, header: str) -> None:
    """全量重写 YAML 文件: 先写头部说明再 dump, 原子替换(先 .tmp 再 os.replace).

    OSError 由调用方处理.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(root, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def ensure_from_template(path: str, template_path, write_skeleton) -> bool:
    """数据文件缺失时播种(优先复制模板), 返回是否执行了写入. 幂等.

    模板缺失/不可复制时退化为调用 write_skeleton(path) 写骨架.
    """
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if template_path and os.path.isfile(template_path):
        try:
            shutil.copyfile(template_path, path)
            return True
        except OSError:
            pass  # 复制失败, 落到写骨架
    write_skeleton(path)
    return True
