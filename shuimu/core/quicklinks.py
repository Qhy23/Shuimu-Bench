"""常用链接纯逻辑: YAML 读写与校验.

不依赖 Qt, GUI 与未来 CLI 共用(对齐 schedule.py). 数据文件为用户可编辑的
纯 YAML; GUI 保存(添加/编辑/删除)会全量重写该文件, 手写注释会丢失.

schema:
  links: [{name, url}]   # name 显示名称(约定唯一, url 须以 http:// 或 https:// 开头)
"""

import os
from dataclasses import dataclass

import yaml

from shuimu.core.yamlio import atomic_write_yaml, ensure_from_template

HEADER = ("# 水木工作台常用链接(name 显示名称, url 须以 http:// 或 https:// 开头).\n"
          "# 注意: GUI 保存会重写本文件, 手写注释会丢失.\n")


@dataclass
class QuickLink:
    """一条常用网站链接."""

    name: str
    url: str


def parse_quick_link(raw) -> QuickLink:
    """解析单条链接, 非法抛 ValueError(带原因)."""
    if not isinstance(raw, dict):
        raise ValueError("条目须为映射")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name 须为非空字符串")
    url = raw.get("url")
    if not isinstance(url, str) or not url.strip().startswith(
            ("http://", "https://")):
        raise ValueError("url 须以 http:// 或 https:// 开头")
    return QuickLink(name.strip(), url.strip())


def load_quick_links(path: str) -> list:
    """读取常用链接 YAML.

    文件缺失返回空(播种归 ensure_links_file); YAML 损坏/顶层非映射/
    'links' 段非列表整体回退为空; 单条非法跳过.
    """
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            root = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(root, dict):
        return []
    raw = root.get("links")
    if not isinstance(raw, list):
        return []
    links = []
    for item in raw:
        try:
            links.append(parse_quick_link(item))
        except ValueError:
            continue
    return links


def save_quick_links(path: str, links: list) -> None:
    """全量重写链接文件. 原子替换: 先写 .tmp 再 os.replace.

    OSError 由调用方(Controller)处理.
    """
    root = {"links": [{"name": link.name, "url": link.url} for link in links]}
    atomic_write_yaml(path, root, HEADER)


def ensure_links_file(path: str, template_path: str = None) -> bool:
    """链接文件缺失时播种(优先复制模板), 返回是否执行了写入. 幂等.

    模板缺失/不可复制时退化为写入仅含头部说明与空段的骨架.
    """
    return ensure_from_template(
        path, template_path, lambda p: save_quick_links(p, []))
