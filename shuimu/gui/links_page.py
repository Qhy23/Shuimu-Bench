"""中栏链接页: 常用网站分组快速访问.

数据经 Controller 增删改查(data/links.yaml, 每次写操作留痕); 分组树是
渲染层聚合(core.quicklinks.grouped_links), 组名不单独持久化; 打开统一走
URL 分发层(默认系统浏览器, 未来浏览器插件可注册内嵌方式与之并存).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from shuimu.core import url_open
from shuimu.core.controller import Controller
from shuimu.core.quicklinks import grouped_links
from shuimu.gui import artwork, theme
from shuimu.gui.dialogs import ok_cancel_box, warn

ROLE_LINK = Qt.UserRole    # 链接叶子携带 QuickLink
ROLE_GROUP = Qt.UserRole + 1  # 组节点携带组名(未分组为空串)

UNGROUPED_LABEL = "未分组"


def _link_icon_size() -> int:
    """链接树图标边长, 随全局字号."""
    return max(12, round(theme.current_font_px() * 1.25))


def _button(text: str, slot) -> QPushButton:
    """创建按钮并连接点击回调."""
    btn: QPushButton = QPushButton(text)
    btn.clicked.connect(slot)
    return btn


class LinkDialog(QDialog):
    """添加/编辑链接对话框: 名称必填, URL 须 http(s) 前缀, 分组可选.

    分组为可编辑下拉: 既有分组可直接选, 也可输入新组名; 留空为未分组.
    default_group 预填当前选中的分组(如"选中某组后添加"的场景).
    """

    def __init__(self, title: str, initial: tuple = None, parent=None,
                 default_group: str = "", group_choices: list = None):
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
        self.group_box = QComboBox(self)
        self.group_box.setEditable(True)
        if group_choices:
            self.group_box.addItems(group_choices)
        self.group_box.setCurrentText(default_group)
        self.group_box.lineEdit().setPlaceholderText(
            "可选, 如: AI工具 (留空为未分组)")
        form.addRow("分组", self.group_box)
        if initial is not None:
            self.name_edit.setText(initial[0])
            self.url_edit.setText(initial[1])
            self.group_box.setCurrentText(
                initial[2] if len(initial) > 2 else "")
        form.addRow(ok_cancel_box(self, self._accept_if_valid))

    def _accept_if_valid(self) -> None:
        """名称为空或 URL 前缀非法时警告且不关闭."""
        name, url, _group = self.values()
        if not name:
            warn(self, "无法保存", "名称不能为空")
            return
        if not url.startswith(("http://", "https://")):
            warn(self, "无法保存", "URL 须以 http:// 或 https:// 开头")
            return
        self.accept()

    def values(self) -> tuple:
        """返回去空白后的 (名称, URL, 分组)."""
        return (self.name_edit.text().strip(), self.url_edit.text().strip(),
                self.group_box.currentText().strip())


class LinksPage(QWidget):
    """链接页(中栏"链接"页签): 分组树 + 增删改 + 打开."""

    def __init__(self, controller: Controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("常用网站: 双击条目或点\"打开\"经系统浏览器访问; "
                      "选中分组后添加可预填分组.", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self.tree)
        row = QHBoxLayout()
        row.addWidget(_button("打开", self._open_current))
        row.addWidget(_button("添加", self._add))
        row.addWidget(_button("编辑", self._edit))
        row.addWidget(_button("删除", self._remove))
        layout.addLayout(row)
        self.reload()

    def reload(self) -> None:
        """从控制器重建分组树(组按首现顺序, 未分组末尾)."""
        self.tree.clear()
        link_icon = QIcon(artwork.kind_icon("link", size=_link_icon_size()))
        group_icon = QIcon(artwork.kind_icon("dir", size=_link_icon_size()))
        for group, links in grouped_links(self.controller.list_quick_links()):
            node = QTreeWidgetItem(self.tree,
                                   [group or UNGROUPED_LABEL])
            node.setData(0, ROLE_GROUP, group)
            node.setIcon(0, group_icon)
            for link in links:
                item = QTreeWidgetItem(node, [f"{link.name} - {link.url}"])
                item.setToolTip(0, link.url)
                item.setData(0, ROLE_LINK, link)
                item.setIcon(0, link_icon)
        self.tree.expandAll()

    def set_font_px(self, _font_px: int) -> None:
        """随全局字号更新两级图标(尺寸读 theme 当前值, 参数保持接口一致)."""
        link_icon = QIcon(artwork.kind_icon("link", size=_link_icon_size()))
        group_icon = QIcon(artwork.kind_icon("dir", size=_link_icon_size()))
        root = self.tree.invisibleRootItem()
        for gi in range(root.childCount()):
            group_node = root.child(gi)
            group_node.setIcon(0, group_icon)
            for li in range(group_node.childCount()):
                group_node.child(li).setIcon(0, link_icon)

    def refresh_theme(self) -> None:
        """主题切换后重建分组树(图标颜色随缓存清除重建)."""
        self.reload()

    def _current_link(self):
        """当前选中链接叶子, 选中组节点或无选中返回 None."""
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, ROLE_LINK)

    def _current_group(self) -> str:
        """当前分组上下文: 组节点返回组名, 链接叶子返回其分组, 其余空串."""
        item = self.tree.currentItem()
        if item is None:
            return ""
        group = item.data(0, ROLE_GROUP)
        if group is not None:
            return group
        link = item.data(0, ROLE_LINK)
        return link.group if link is not None else ""

    def _group_choices(self) -> list:
        """既有分组名(不含未分组), 供对话框下拉建议."""
        return [group for group, _links
                in grouped_links(self.controller.list_quick_links()) if group]

    def _require_selection(self) -> bool:
        """无选中链接时提示并返回 False."""
        if self._current_link() is None:
            warn(self, "未选中", "请先在树中选中一条链接")
            return False
        return True

    def _open_current(self) -> None:
        """打开当前选中链接(经 URL 分发层)."""
        if not self._require_selection():
            return
        self._open_link(self._current_link())

    def _on_double_clicked(self, item, _column) -> None:
        """双击链接叶子打开; 双击组节点交给树自身折叠展开."""
        link = item.data(0, ROLE_LINK)
        if link is not None:
            self._open_link(link)

    def _open_link(self, link) -> None:
        """打开一条链接(经 URL 分发层)."""
        if not url_open.open_url(link.url):
            warn(self, "无法打开链接", link.url)

    def _add(self) -> None:
        """添加链接: 预填当前分组上下文, 成功后刷新树."""
        dialog = LinkDialog("添加链接", parent=self,
                            default_group=self._current_group(),
                            group_choices=self._group_choices())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.controller.add_quick_link(*dialog.values())
        if not result.ok:
            warn(self, "链接操作失败", result.message)
            return
        self.reload()

    def _edit(self) -> None:
        """编辑选中链接(含改分组)."""
        if not self._require_selection():
            return
        link = self._current_link()
        dialog = LinkDialog("编辑链接", initial=(link.name, link.url,
                                                 link.group),
                            parent=self,
                            group_choices=self._group_choices())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.controller.update_quick_link(link.name,
                                                    *dialog.values())
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
