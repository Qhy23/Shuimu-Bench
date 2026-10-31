"""中栏链接页: 常用网站快速访问.

数据经 Controller 增删改查(data/links.yaml, 每次写操作留痕); 打开统一走
URL 分发层(默认系统浏览器, 未来浏览器插件可注册内嵌方式与之并存).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from shuimu.core import url_open
from shuimu.core.controller import Controller
from shuimu.gui import artwork, theme
from shuimu.gui.dialogs import ok_cancel_box, warn

ROLE_LINK = Qt.UserRole  # 列表项携带 QuickLink


def _link_icon_size() -> int:
    """链接列表图标边长, 随全局字号."""
    return max(12, round(theme.current_font_px() * 1.25))


def _button(text: str, slot) -> QPushButton:
    """创建按钮并连接点击回调."""
    btn: QPushButton = QPushButton(text)
    btn.clicked.connect(slot)
    return btn


class LinkDialog(QDialog):
    """添加/编辑链接对话框: 名称必填, URL 须 http(s) 前缀."""

    def __init__(self, title: str, initial: tuple = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("必填, 如: 网络学堂")
        form.addRow("名称", self.name_edit)
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(
            "必填, 如: https://learn.tsinghua.edu.cn")
        form.addRow("URL", self.url_edit)
        if initial is not None:
            self.name_edit.setText(initial[0])
            self.url_edit.setText(initial[1])
        form.addRow(ok_cancel_box(self, self._accept_if_valid))

    def _accept_if_valid(self) -> None:
        """名称为空或 URL 前缀非法时警告且不关闭."""
        name, url = self.values()
        if not name:
            warn(self, "无法保存", "名称不能为空")
            return
        if not url.startswith(("http://", "https://")):
            warn(self, "无法保存", "URL 须以 http:// 或 https:// 开头")
            return
        self.accept()

    def values(self) -> tuple:
        """返回去空白后的 (名称, URL)."""
        return self.name_edit.text().strip(), self.url_edit.text().strip()


class LinksPage(QWidget):
    """链接页(中栏"链接"页签)."""

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("常用网站: 双击条目或点\"打开\"经系统浏览器访问.", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list_widget = QListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.list_widget)
        row = QHBoxLayout()
        row.addWidget(_button("打开", self._open_current))
        row.addWidget(_button("添加", self._add))
        row.addWidget(_button("编辑", self._edit))
        row.addWidget(_button("删除", self._remove))
        layout.addLayout(row)
        self.reload()

    def reload(self) -> None:
        """从控制器重建链接列表."""
        self.list_widget.clear()
        icon = QIcon(artwork.kind_icon("link", size=_link_icon_size()))
        for link in self.controller.list_quick_links():
            item = QListWidgetItem(icon, f"{link.name} - {link.url}")
            item.setToolTip(link.url)
            item.setData(ROLE_LINK, link)
            self.list_widget.addItem(item)

    def set_font_px(self, _font_px: int) -> None:
        """随全局字号更新列表图标(尺寸读 theme 当前值, 参数保持接口一致)."""
        icon = QIcon(artwork.kind_icon("link", size=_link_icon_size()))
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setIcon(icon)

    def _current_link(self):
        """当前选中条目的 QuickLink, 无选中返回 None."""
        item = self.list_widget.currentItem()
        return item.data(ROLE_LINK) if item is not None else None

    def _require_selection(self) -> bool:
        """无选中时提示并返回 False."""
        if self._current_link() is None:
            warn(self, "未选中", "请先在列表中选中一条链接")
            return False
        return True

    def _open_current(self) -> None:
        """打开当前选中链接(经 URL 分发层)."""
        if not self._require_selection():
            return
        self._open_item(self.list_widget.currentItem())

    def _open_item(self, item) -> None:
        """打开列表条目对应的链接."""
        link = item.data(ROLE_LINK)
        if not url_open.open_url(link.url):
            warn(self, "无法打开链接", link.url)

    def _add(self) -> None:
        """添加链接: 对话框录入, 成功后刷新列表."""
        dialog = LinkDialog("添加链接", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.controller.add_quick_link(*dialog.values())
        if not result.ok:
            warn(self, "链接操作失败", result.message)
            return
        self.reload()

    def _edit(self) -> None:
        """编辑选中链接."""
        if not self._require_selection():
            return
        link = self._current_link()
        dialog = LinkDialog("编辑链接", initial=(link.name, link.url),
                            parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.controller.update_quick_link(link.name, *dialog.values())
        if not result.ok:
            warn(self, "链接操作失败", result.message)
            return
        self.reload()

    def _remove(self) -> None:
        """删除选中链接(带确认)."""
        if not self._require_selection():
            return
        link = self._current_link()
        answer = QMessageBox.question(
            self, "删除链接", f"删除「{link.name}」({link.url})?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.controller.remove_quick_link(link.name)
        if not result.ok:
            warn(self, "链接操作失败", result.message)
            return
        self.reload()
