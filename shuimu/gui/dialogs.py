"""共享对话框辅助: 各 GUI 模块通用的弹窗函数与对话框类."""

import os
import time

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from shuimu.core.actions import ActionResult
from shuimu.core.fileops import find_conflicts


def warn(parent, title: str, text: str) -> None:
    """弹出警告对话框(独立成函数便于测试替换)."""
    QMessageBox.warning(parent, title, text)


def ask_conflict(name: str, remaining: int, parent) -> tuple:
    """单个同名冲突决策框. 返回 (决策, 是否应用于其余全部).

    decision: replace(旧的移入回收站)/skip/keep_both(自动加 _1)/cancel.
    """
    box = QMessageBox(parent)
    box.setWindowTitle("同名冲突")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(f"“{name}” 在目标文件夹已存在。")
    box.setInformativeText("选择如何处理此项。")
    check = None
    if remaining > 1:
        # QCheckBox 须持局部引用: shiboken6 下无父 QObject 由 Python
        # 包装器拥有, 临时对象在语句结束即被回收, 对话框内成悬空指针
        check = QCheckBox(f"应用于其余 {remaining - 1} 个冲突")
        box.setCheckBox(check)
    btn_replace = box.addButton("覆盖(旧的移入回收站)",
                                QMessageBox.ButtonRole.YesRole)
    btn_skip = box.addButton("跳过", QMessageBox.ButtonRole.NoRole)
    btn_keep = box.addButton("两者都保留(自动加 _1)",
                             QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(btn_keep)
    box.exec()
    clicked = box.clickedButton()
    if clicked is btn_replace:
        choice = "replace"
    elif clicked is btn_skip:
        choice = "skip"
    elif clicked is btn_keep:
        choice = "keep_both"
    else:
        choice = "cancel"   # Esc 或关闭框
    apply_all = bool(check is not None and check.isChecked())
    return choice, apply_all


def resolve_conflicts(conflicts: list, ask) -> dict:
    """逐个询问同名冲突的处理方式.

    ask(name, remaining) -> (choice, apply_all) 由调用方注入(便于测试
    替换). 返回 {src: "replace"|"skip"|"keep_both"}, 取消整个粘贴返回
    None; 勾选"应用于其余全部"后其余冲突沿用当前决策.
    """
    policies = {}
    apply_all = None
    for i, (src, _dst) in enumerate(conflicts):
        if apply_all is None:
            choice, apply = ask(os.path.basename(src), len(conflicts) - i)
            if choice == "cancel":
                return None
            policies[src] = choice
            if apply:
                apply_all = choice
        else:
            policies[src] = apply_all
    return policies


def perform_paste(ctl, clipboard: dict, target: str, ask,
                  register=None) -> ActionResult:
    """应用内剪贴板粘贴内核(左栏课程树与中栏文件页共用).

    同名冲突逐个询问(ask 注入) -> 覆盖项先入回收站(索引行同步移除) ->
    复制或移动到 target; register(dst) 在每个成功项后回调(左栏用它把
    产物登记进课程, 返回是否登记成功). 取消或全跳过返回 ok=False 的
    结果, 不动磁盘. 剪切分支只移出成功项并保留剩余可重试.
    """
    paths = clipboard["paths"]
    policies = {}
    conflicts = find_conflicts(paths, target)
    if conflicts:
        policies = resolve_conflicts(conflicts, ask)
        if policies is None:
            return ActionResult(False, "已取消粘贴")
    skip_srcs = {src for src, policy in policies.items() if policy == "skip"}
    replace_dsts = [dst for src, dst in conflicts
                    if policies.get(src) == "replace"]
    if replace_dsts:
        ctl.file_delete(replace_dsts)
    todo = [p for p in paths if p not in skip_srcs]
    if not todo:
        return ActionResult(False, "已跳过全部项目")
    action = ctl.file_copy if clipboard["mode"] == "copy" else ctl.file_move
    result = action(todo, target)
    if register is not None:
        registered = sum(
            1 for item in result.detail.get("items", [])
            if item["status"] == "ok" and register(item["dst"]))
        result = ActionResult(result.ok, f"{result.message}, 已登记 {registered} 项",
                              result.detail)
    if clipboard["mode"] == "cut":
        done = {item["src"] for item in result.detail.get("items", [])
                if item["status"] == "ok"}
        remaining = [p for p in clipboard["paths"] if p not in done]
        clipboard["mode"] = "cut" if remaining else None
        clipboard["paths"] = remaining
    return result


def ok_cancel_box(parent, on_accept) -> QDialogButtonBox:
    """Ok/Cancel 按钮组: Ok 触发 on_accept(校验后 accept), Cancel 关闭对话框."""
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel, parent)
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
