"""URL 打开分发层: 默认处理器 + 具名处理器注册表.

不依赖 Qt, 供 GUI/CLI 与未来插件共用. core 层不绑定任何实现:
默认处理器(系统浏览器)由 GUI 入口(shuimu.gui.main_window)在模块导入时
经 set_default 注册; 未来浏览器插件可经 register 注册内嵌打开方式,
与系统浏览器并存, openers() 供"打开方式"菜单枚举可选项.
"""

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
