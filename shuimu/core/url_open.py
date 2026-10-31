"""URL 打开分发层: 默认处理器 + 具名处理器注册表 + 系统打开的标准库实现.

不依赖 Qt, 供 GUI/CLI 与未来插件共用. core 层不绑定任何默认处理器:
GUI 入口(shuimu.gui.main_window)在模块导入时注册 QDesktopServices 版
(wslview 优先); CLI 入口(shuimu.cli.main)注册 open_url_external 纯
标准库版; 未来浏览器插件可经 register 注册内嵌打开方式与之并存,
openers() 供"打开方式"菜单枚举可选项.
"""

import os
import platform
import shutil
import subprocess
import sys


def _no_opener(_url: str) -> bool:
    """默认处理器未注册时的占位: 打开恒失败."""
    return False


_state = {"default": _no_opener, "registry": {}}  # 本模块单点写入


def set_default(fn) -> None:
    """设置默认打开处理器 fn(url) -> bool."""
    _state["default"] = fn


def register(key: str, label: str, fn) -> None:
    """注册具名打开方式. fn 传 None 表示移除该 key; 同 key 重复注册以后者为准."""
    registry = _state["registry"]
    if fn is None:
        registry.pop(key, None)
        return
    registry[key] = (label, fn)


def openers() -> list:
    """已注册的打开方式 [(key, label)], 供菜单枚举."""
    return [(key, label)
            for key, (label, _fn) in _state["registry"].items()]


def open_url(url: str, key: str = None) -> bool:
    """按指定方式打开 URL; key 为 None 走默认处理器.

    未注册/未设置默认返回 False, 由调用方决定提示.
    """
    if key is not None:
        entry = _state["registry"].get(key)
        if entry is None:
            return False
        return bool(entry[1](url))
    opener = _state["default"]
    if opener is None:
        return False
    return bool(opener(url))


# ---- 系统打开的标准库实现(GUI 的 wslview 分发同源, 供 CLI 等无 Qt 入口) ----

def in_wsl() -> bool:
    """是否运行在 WSL 内(内核版本号含 microsoft 或设置 WSL_DISTRO_NAME)."""
    return "microsoft" in platform.release().lower() or bool(
        os.environ.get("WSL_DISTRO_NAME"))


def wslview_open(target: str):
    """WSL 内优先用 wslview 打开, 转发给 Windows 默认程序.

    WSL 里 xdg-open 的默认关联常指向 snap 应用(如 chromium), snapd 在
    WSL 中不可用导致打开失败; wslview 直接走 Windows 文件关联.
    非 WSL 或未安装 wslview 时返回 None, 由调用方回退其他方式.
    """
    if not in_wsl():
        return None
    wslview = shutil.which("wslview")
    if not wslview:
        return None
    try:
        proc = subprocess.run([wslview, target], check=False, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def open_url_external(url: str) -> bool:
    """系统浏览器打开 URL 的纯标准库实现(CLI 入口注册为默认处理器).

    WSL 优先 wslview; 原生平台按惯例选 open(macOS) / os.startfile
    (Windows) / xdg-open(Linux).
    """
    if in_wsl():
        return wslview_open(url) is True
    if sys.platform == "win32":
        try:
            os.startfile(url)
        except OSError:
            return False
        return True
    return _run_opener("open" if sys.platform == "darwin" else "xdg-open",
                       url)


def _run_opener(tool: str, url: str) -> bool:
    """经 PATH 上的工具打开 URL, 不可用或超时返回 False."""
    exe = shutil.which(tool)
    if not exe:
        return False
    try:
        proc = subprocess.run([exe, url], check=False, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0
