"""文件类型图标: 系统图标优先, 内置彩色扩展名图标回退.

QFileSystemModel 的 Type 列图标本就走系统 QFileIconProvider; 课程树
需要自行补图标. Windows 上沿用资源管理器图标; 无图标的 offscreen/精简
桌面环境按扩展名绘制, 不依赖第三方资源包或联网.
"""

import functools
import os

from PySide6.QtCore import QFileInfo, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFileIconProvider

# 扩展名 -> (简短标记, 图标底色). 以常见文件类型分组, 避免满屏彩色噪音.
_TYPE_STYLES = {
    "pdf": ("PDF", "#d64545"),
    "doc": ("DOC", "#3b78c3"), "docx": ("DOC", "#3b78c3"),
    "odt": ("DOC", "#3b78c3"), "rtf": ("DOC", "#3b78c3"),
    "xls": ("XLS", "#31845b"), "xlsx": ("XLS", "#31845b"),
    "csv": ("CSV", "#31845b"), "ods": ("XLS", "#31845b"),
    "ppt": ("PPT", "#cf6a32"), "pptx": ("PPT", "#cf6a32"),
    "txt": ("TXT", "#78838f"), "md": ("MD", "#596579"),
    "py": ("PY", "#4a8cb3"), "js": ("JS", "#b59a28"),
    "ts": ("TS", "#437db5"), "html": ("<>", "#cf6a32"),
    "css": ("CSS", "#437db5"), "json": ("{}", "#b59a28"),
    "yaml": ("YML", "#78838f"), "yml": ("YML", "#78838f"),
    "xml": ("XML", "#78838f"), "c": ("C", "#596579"),
    "h": ("H", "#596579"), "cpp": ("C++", "#596579"),
    "java": ("J", "#b85c42"), "sh": ("SH", "#78838f"),
    "zip": ("ZIP", "#a58542"), "rar": ("RAR", "#a58542"),
    "7z": ("7Z", "#a58542"), "tar": ("TAR", "#a58542"),
    "gz": ("GZ", "#a58542"),
    "png": ("IMG", "#497ea0"), "jpg": ("IMG", "#497ea0"),
    "jpeg": ("IMG", "#497ea0"), "gif": ("IMG", "#497ea0"),
    "bmp": ("IMG", "#497ea0"), "svg": ("SVG", "#8a65a5"),
    "webp": ("IMG", "#497ea0"), "ico": ("ICO", "#497ea0"),
    "mp3": ("MP3", "#8a65a5"), "wav": ("WAV", "#8a65a5"),
    "flac": ("FLAC", "#8a65a5"), "mp4": ("MP4", "#8a65a5"),
    "mkv": ("MKV", "#8a65a5"), "mov": ("MOV", "#8a65a5"),
    "avi": ("AVI", "#8a65a5"),
}


def extension(path: str) -> str:
    """路径的小写扩展名(不含点), 无扩展名返回空串."""
    return os.path.splitext(path)[1].lstrip(".").lower()


@functools.lru_cache(maxsize=128)
def file_type_icon(path: str, size: int = 16, failed: bool = False) -> QIcon:
    """按文件后缀返回类型图标, 结果按路径/尺寸/失效态缓存."""
    if not failed:
        system = QFileIconProvider().icon(QFileInfo(path))
        if not system.isNull():
            return system
    label, color = _TYPE_STYLES.get(extension(path), ("FILE", "#78838f"))
    if failed:
        color = "#78838f"
    return QIcon(_draw_type_icon(label, color, size))


def _draw_type_icon(label: str, color: str, size: int) -> QPixmap:
    """画 2x 彩色类型标签, 平滑缩放到目标尺寸."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = size * 2.0
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, int(side) - 2, int(side) - 2,
                            max(2, size // 4), max(2, size // 4))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(max(7, int(size * (0.42 if len(label) > 2 else 0.5))))
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, label)
    painter.end()
    return pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


def clear_cache() -> None:
    """清空按尺寸缓存的扩展名图标."""
    file_type_icon.cache_clear()
