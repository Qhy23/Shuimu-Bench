"""共享对话框辅助: 各 GUI 模块通用的弹窗函数(对话框类后续也可迁入)."""

from PySide6.QtWidgets import QMessageBox


def warn(parent, title: str, text: str) -> None:
    """弹出警告对话框(独立成函数便于测试替换)."""
    QMessageBox.warning(parent, title, text)
