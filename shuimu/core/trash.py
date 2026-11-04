"""回收站后端: 把文件/目录移入回收站, 多后端分发.

优先级: config.yaml fileops.trash_dir 指定的目录 > 平台原生.
- config_dir: 配置目录自管理(WSL 等无桌面回收站的环境用),
  布局与 freedesktop 一致(files/ + info/*.trashinfo), 可与 XDG 目录互操作;
- xdg: Linux/macOS 按 freedesktop Trash 规范落 $XDG_DATA_HOME/Trash
  (缺省 ~/.local/share/Trash), 写 .trashinfo 备将来恢复;
- windows_shell: Windows 经 ctypes 调 SHFileOperationW 送系统回收站.
全部标准库实现, 不引第三方依赖.
"""

import ctypes
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime

from shuimu.core.config import load_config, trash_dir_value
from shuimu.core.fileops import dedupe_path
from shuimu.core.paths import project_root

BACKEND_CONFIG_DIR = "config_dir"
BACKEND_WINDOWS_SHELL = "windows_shell"
BACKEND_XDG = "xdg"


@dataclass
class TrashResult:
    """一次入回收站的结果."""

    backend: str
    trash_path: str   # 回收站侧路径; windows_shell 后端不回传, 为 ""


def trash_backend(cfg: dict = None, platform: str = None) -> str:
    """决定回收站后端(纯函数, platform 供测试注入).

    配置了 trash_dir 一律优先; 其余按平台: win32 -> Shell 回收站,
    其他(Linux/macOS/WSL) -> XDG Trash.
    """
    if cfg is None:
        cfg = load_config()
    if trash_dir_value(cfg):
        return BACKEND_CONFIG_DIR
    if (platform or sys.platform) == "win32":
        return BACKEND_WINDOWS_SHELL
    return BACKEND_XDG


def resolve_trash_root(cfg: dict = None) -> str:
    """回收站根目录(config_dir 与 xdg 后端). windows_shell 不会走到这里."""
    backend = trash_backend(cfg)
    if backend == BACKEND_CONFIG_DIR:
        raw = os.path.expanduser(trash_dir_value(cfg or load_config()))
        if not os.path.isabs(raw):
            # 相对路径按项目根解析, 不随启动 CWD 漂移
            raw = os.path.join(project_root(), raw)
        return os.path.abspath(raw)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "Trash")


def move_to_trash(path: str, cfg: dict = None) -> TrashResult:
    """把一个文件/目录移入回收站. 失败抛 OSError(带路径与原因)."""
    backend = trash_backend(cfg)
    if backend == BACKEND_WINDOWS_SHELL:
        return _windows_shell_trash(path)
    return _trash_to_dir(path, resolve_trash_root(cfg), backend)


def _trash_to_dir(path: str, root: str, backend: str) -> TrashResult:
    """移入自管理目录, 布局按 freedesktop Trash 规范.

    files/ 存本体, info/ 存同名 .trashinfo(原路径 + 删除时间),
    同名冲突经 dedupe_path 加后缀, files 与 info 保持同名.
    """
    files_dir = os.path.join(root, "files")
    info_dir = os.path.join(root, "info")
    os.makedirs(files_dir, exist_ok=True)
    os.makedirs(info_dir, exist_ok=True)
    name = os.path.basename(dedupe_path(os.path.join(
        files_dir, os.path.basename(path.rstrip(os.sep)))))
    dst = os.path.join(files_dir, name)
    original = os.path.abspath(path)
    shutil.move(path, dst)
    info_file = os.path.join(info_dir, name + ".trashinfo")
    with open(info_file, "w", encoding="utf-8") as f:
        f.write("[Trash Info]\n")
        f.write(f"Path={_encode_path(original)}\n")
        f.write(f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    return TrashResult(backend=backend, trash_path=dst)


def _encode_path(path: str) -> str:
    """按 freedesktop 规范转义 Path 值中的保留字符(% # ?)."""
    return path.replace("%", "%25").replace("#", "%23").replace("?", "%3F")


class _SHFILEOPSTRUCTW(ctypes.Structure):
    """SHFileOperationW 参数结构(仅 Windows 后端使用).

    字段名保持 Win32 API 原名以便对照官方文档, 不合命名约定, 类内豁免.
    """

    # pylint: disable=invalid-name
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


# SHFileOperationW 常量(官方文档值)
_FO_DELETE = 3
_FOF_SILENT = 0x4
_FOF_NOCONFIRMATION = 0x10
_FOF_ALLOWUNDO = 0x40
_FOF_NOERRORUI = 0x400


def _windows_shell_trash(path: str) -> TrashResult:
    """Windows 系统回收站(薄平台层).

    仅原生 Windows 可执行, WSL/Linux 下无法运行也不单测;
    可测的分发决策在 trash_backend, 本函数保持最小逻辑:
    结构体拼装 + 一次调用 + 错误转译.
    """
    op = _SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=_FO_DELETE,
        pFrom=os.path.abspath(path) + "\0",  # c_wchar_p 再补一个 NUL, 凑双结尾
        pTo=None,
        fFlags=(_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION |
                _FOF_SILENT | _FOF_NOERRORUI),
        fAnyOperationsAborted=0,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0:
        raise OSError(f"SHFileOperationW 失败(码 {code}): {path}")
    return TrashResult(backend=BACKEND_WINDOWS_SHELL, trash_path="")
