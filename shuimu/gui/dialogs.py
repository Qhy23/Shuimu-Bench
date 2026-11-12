"""共享对话框辅助: 各 GUI 模块通用的弹窗函数与对话框类."""

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from shuimu.gui import theme


def warn(parent, title: str, text: str) -> None:
    """弹出警告对话框(独立成函数便于测试替换)."""
    QMessageBox.warning(parent, title, text)


def ok_cancel_box(parent, on_accept) -> QDialogButtonBox:
    """Ok/Cancel 按钮组: Ok 挂 primary 样式, 触发 on_accept(校验后 accept),
    Cancel 关闭对话框."""
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel, parent)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty(
        "primary", True)
    buttons.accepted.connect(on_accept)
    buttons.rejected.connect(parent.reject)
    return buttons


def confirm_network_enable(parent, manifest) -> bool:
    """network 插件启用确认框(启用即授权该插件访问在线服务)."""
    answer = QMessageBox.question(
        parent, "网络权限确认",
        f"插件「{manifest.name}」声明了网络权限.\n"
        "启用后允许它访问对应的在线服务(如学校统一认证). 启用?")
    return answer == QMessageBox.StandardButton.Yes


def _status_color(status: str) -> QColor:
    """留痕状态 -> 前景色(ok 绿 / failed 红 / 其余次要色)."""
    tk = theme.tokens()
    if status == "ok":
        return QColor(tk.success)
    if status == "failed":
        return QColor(tk.danger)
    return QColor(tk.fg_dim)


def format_operation_line(op) -> str:
    """留痕行格式化: [MM-DD HH:MM:SS] status action path_snapshot detail."""
    ts = time.strftime("%m-%d %H:%M:%S", time.localtime(op["started_at"]))
    parts = [f"[{ts}]", op["status"], op["action"]]
    if op["path_snapshot"]:
        parts.append(op["path_snapshot"])
    if op["detail"]:
        parts.append(str(op["detail"]))
    return " ".join(parts)


def _detail_text(op) -> str:
    """留痕详情列文本: 路径快照 + detail JSON 拼接."""
    parts = []
    if op["path_snapshot"]:
        parts.append(op["path_snapshot"])
    if op["detail"]:
        parts.append(str(op["detail"]))
    return "  ".join(parts)


class LogPreviewDialog(QDialog):
    """操作留痕预览: 四列着色表格(时间/动作/状态/路径与详情), 只读.

    设计为非模态常开(主窗口持引用), 留痕由各操作写入后点刷新查看.
    """

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("操作留痕")
        self.storage = storage
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            ["时间", "动作", "状态", "路径与详情"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        row.addStretch(1)
        refresh_btn = QPushButton("刷新", self)
        refresh_btn.setProperty("tool", True)
        refresh_btn.clicked.connect(self.refresh)
        row.addWidget(refresh_btn)
        close_btn = QPushButton("关闭", self)
        close_btn.setProperty("tool", True)
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def refresh(self) -> None:
        """从 operations 表重建预览内容(新到旧)."""
        ops = self.storage.list_operations()
        self.table.setRowCount(len(ops))
        for row, op in enumerate(ops):
            ts = time.strftime("%m-%d %H:%M:%S",
                               time.localtime(op["started_at"]))
            self.table.setItem(row, 0, QTableWidgetItem(ts))
            self.table.setItem(row, 1, QTableWidgetItem(op["action"]))
            status = QTableWidgetItem(op["status"])
            status.setForeground(_status_color(op["status"]))
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 2, status)
            detail = QTableWidgetItem(_detail_text(op))
            detail.setToolTip(_detail_text(op))
            self.table.setItem(row, 3, detail)
