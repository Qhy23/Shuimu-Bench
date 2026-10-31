"""共享对话框辅助: 各 GUI 模块通用的弹窗函数与对话框类."""

import time

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


def warn(parent, title: str, text: str) -> None:
    """弹出警告对话框(独立成函数便于测试替换)."""
    QMessageBox.warning(parent, title, text)


def ok_cancel_box(parent, on_accept) -> QDialogButtonBox:
    """Ok/Cancel 按钮组: Ok 触发 on_accept(校验后 accept), Cancel 关闭对话框."""
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel, parent)
    buttons.accepted.connect(on_accept)
    buttons.rejected.connect(parent.reject)
    return buttons


def format_operation_line(op) -> str:
    """留痕行格式化: [MM-DD HH:MM:SS] status action path_snapshot detail."""
    ts = time.strftime("%m-%d %H:%M:%S", time.localtime(op["started_at"]))
    parts = [f"[{ts}]", op["status"], op["action"]]
    if op["path_snapshot"]:
        parts.append(op["path_snapshot"])
    if op["detail"]:
        parts.append(str(op["detail"]))
    return " ".join(parts)


class LogPreviewDialog(QDialog):
    """操作留痕预览: 最近若干条, 只读, 带手动刷新.

    设计为非模态常开(主窗口持引用), 留痕由各操作写入后点刷新查看.
    """

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("操作留痕")
        self.storage = storage
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        self.view = QPlainTextEdit(readOnly=True)
        layout.addWidget(self.view)
        row = QHBoxLayout()
        refresh_btn = QPushButton("刷新", self)
        refresh_btn.clicked.connect(self.refresh)
        row.addWidget(refresh_btn)
        close_btn = QPushButton("关闭", self)
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def refresh(self) -> None:
        """从 operations 表重建预览内容."""
        lines = [format_operation_line(op)
                 for op in self.storage.list_operations()]
        self.view.setPlainText("\n".join(lines) or "(暂无操作记录)")
