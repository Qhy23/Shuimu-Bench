"""插件运行 GUI 编排: 声明式参数对话框 + 结果对话框 + 写权限确认.

对话框全部复用 theme 现有 QSS; 严重度着色用前景色, 不新增样式规则.
表单按 manifest.params 声明生成, 插件自身不写任何 UI(契约见
core/pluginapi.py).
"""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shuimu.core.controller import ActionResult
from shuimu.core.pluginapi import SEVERITY_LABEL, PluginReport
from shuimu.core.pluginman import PluginManifest, validate_params
from shuimu.gui import theme
from shuimu.gui.dialogs import ok_cancel_box, warn


def _severity_color(severity: str) -> QColor:
    """严重度 -> 前景色(错误警示红, 警告橙, 通过用次要文字色)."""
    if severity == "error":
        return QColor(theme.DANGER)
    if severity == "warn":
        return QColor(theme.WARN)
    return QColor(theme.FG_DIM)


class PluginRunDialog(QDialog):
    """按 manifest.params 声明式生成表单的插件参数对话框."""

    def __init__(self, manifest: PluginManifest, targets: list, parent=None):
        super().__init__(parent)
        self._manifest = manifest
        self._fields = {}   # 参数名 -> 取值控件
        self.setWindowTitle(f"运行插件 - {manifest.name}")
        self.resize(480, 340)
        layout = QVBoxLayout(self)
        if manifest.description:
            desc = QLabel(manifest.description)
            desc.setWordWrap(True)
            layout.addWidget(desc)
        text = "\n".join(targets[:3])
        if len(targets) > 3:
            text += f"\n...等共 {len(targets)} 项"
        target_label = QLabel(f"目标:\n{text}")
        target_label.setWordWrap(True)
        layout.addWidget(target_label)
        form = QFormLayout()
        for param in manifest.params:
            value_widget, form_widget = self._make_field(param)
            self._fields[param.name] = value_widget
            form.addRow(param.label + (" *" if param.required else ""),
                        form_widget)
        layout.addLayout(form)
        layout.addWidget(ok_cancel_box(self, self._accept))

    def _make_field(self, param) -> tuple:
        """类型 -> (取值控件, 挂表单控件); path 为 输入框+浏览 组合."""
        if param.type == "secret":
            edit = QLineEdit(_default_text(param))
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setToolTip(param.help)
            return edit, edit
        if param.type == "str":
            edit = QLineEdit(_default_text(param))
            edit.setToolTip(param.help)
            return edit, edit
        if param.type == "path":
            edit = QLineEdit(_default_text(param))
            edit.setToolTip(param.help)
            wrap = QWidget()
            row = QHBoxLayout(wrap)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(edit)
            browse = QPushButton("浏览...")
            browse.clicked.connect(lambda: self._pick_path(edit))
            row.addWidget(browse)
            return edit, wrap
        if param.type == "int":
            spin = QSpinBox()
            spin.setRange(0, 10000000)
            spin.setValue(int(param.default or 0))
            spin.setToolTip(param.help)
            return spin, spin
        if param.type == "choice":
            combo = QComboBox()
            for value, label in param.choices:
                combo.addItem(label, value)
            if param.default is not None:
                combo.setCurrentIndex(max(combo.findData(param.default), 0))
            combo.setToolTip(param.help)
            return combo, combo
        check = QCheckBox()
        check.setChecked(bool(param.default))
        check.setToolTip(param.help)
        return check, check

    def _pick_path(self, edit: QLineEdit) -> None:
        """浏览选择文件并回填输入框."""
        path, _selected = QFileDialog.getOpenFileName(self, "选择文件",
                                                      edit.text())
        if path:
            edit.setText(path)

    def values(self) -> dict:
        """各控件当前值(choice 取候选值, 其余取控件原生值)."""
        params = {}
        for param in self._manifest.params:
            field = self._fields[param.name]
            if param.type == "bool":
                params[param.name] = field.isChecked()
            elif param.type == "choice":
                params[param.name] = field.currentData()
            elif param.type == "int":
                params[param.name] = field.value()
            else:
                params[param.name] = field.text()
        return params

    def _accept(self) -> None:
        """校验通过才关闭; 有错误弹提示留在对话框."""
        _, errors = validate_params(self._manifest, self.values())
        if errors:
            warn(self, "参数无效", "\n".join(errors))
            return
        self.accept()


class PluginResultDialog(QDialog):
    """插件结果: 摘要 + 计数 + findings 表格 + 产出清单."""

    def __init__(self, plugin_name: str, report: PluginReport, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{plugin_name} 结果")
        self.resize(680, 440)
        layout = QVBoxLayout(self)
        summary = QLabel(report.summary)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        counts = {sev: sum(1 for f in report.findings if f.severity == sev)
                  for sev in ("error", "warn", "ok")}
        layout.addWidget(QLabel(
            f"共 {len(report.findings)} 项: {counts['error']} 错误 / "
            f"{counts['warn']} 警告 / {counts['ok']} 通过"))
        self.table = QTableWidget(len(report.findings), 4, self)
        self.table.setHorizontalHeaderLabels(["严重度", "检查项", "结论", "详情"])
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 200)
        for row, item in enumerate(report.findings):
            severity = QTableWidgetItem(SEVERITY_LABEL[item.severity])
            severity.setForeground(_severity_color(item.severity))
            self.table.setItem(row, 0, severity)
            self.table.setItem(row, 1, QTableWidgetItem(item.check))
            title = QTableWidgetItem(item.title)
            title.setToolTip(item.title)
            self.table.setItem(row, 2, title)
            detail = QTableWidgetItem(item.detail)
            detail.setToolTip(item.detail)
            self.table.setItem(row, 3, detail)
            self.table.resizeRowToContents(row)
        layout.addWidget(self.table)
        if report.outputs:
            layout.addWidget(QLabel("产出:\n" + "\n".join(report.outputs)))
        close = QPushButton("关闭", self)
        close.clicked.connect(self.reject)
        layout.addWidget(close)


def confirm_write(parent, manifest: PluginManifest) -> bool:
    """write 权限确认框(插件声明写权限时, 运行前必过)."""
    answer = QMessageBox.question(
        parent, "写权限确认",
        f"插件「{manifest.name}」声明了写权限, 允许它修改/生成文件?")
    return answer == QMessageBox.StandardButton.Yes


def run_plugin_dialogs(parent, ctl, record, paths: list) -> ActionResult:
    """编排一次 GUI 插件运行: 参数框 -> 写确认 -> 运行 -> 结果框.

    参数框取消时记一次 cancelled 留痕并返回失败结果(不弹错误框);
    运行失败无报告时弹警告; 有报告一律弹结果框(检查项问题不算失败).
    """
    manifest = record.manifest
    dialog = PluginRunDialog(manifest, paths, parent)
    if not dialog.exec():
        ctl.cancel_plugin(manifest.id, paths)
        return ActionResult(False, f"已取消插件 {manifest.name}")
    confirm = (lambda m: confirm_write(parent, m)) \
        if "write" in manifest.permissions else None
    result = ctl.run_plugin(manifest.id, paths, dialog.values(),
                            confirm=confirm)
    report = result.detail.get("report")
    if report is not None:
        PluginResultDialog(manifest.name, report, parent).exec()
    elif not result.ok:
        warn(parent, "插件运行失败", result.message)
    return result


def _default_text(param) -> str:
    """str/path 参数的默认文本(None -> 空)."""
    return str(param.default) if param.default is not None else ""
