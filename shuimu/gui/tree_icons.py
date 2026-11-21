"""课程树图标的轻量刷新, 与索引重建分离.

字号变化只改变图标尺寸, 不应重查数据库或磁盘. 树节点在构建时写入
类型与失效状态; 本模块据此原地换图, 保留节点身份与展开状态.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon

from shuimu.gui import artwork, file_icons, theme

ROLE_COURSE_ID = Qt.UserRole + 1
ROLE_RESOURCE_KIND = Qt.UserRole + 3  # 展示所需资源类型, 避免图标刷新回查 DB
ROLE_RESOURCE_FAILED = Qt.UserRole + 4  # 加载时判定的失效状态
ROLE_RESOURCE_PATH = Qt.UserRole + 6  # 文件后缀/链接 URL 的轻量展示信息
_ROLE_ICON_KEY = Qt.UserRole + 5  # (图标尺寸, 主题代次), 避免重复重绘


def tree_icon_size() -> int:
    """课程树节点图标边长, 随全局字号."""
    return max(12, round(theme.current_font_px() * 1.25))


def _update_item_icon(item, icon_size: int, revision: int,
                      favicon_store) -> None:
    """按节点轻量展示信息刷新图标, 不触发业务查询."""
    kind = item.data(0, ROLE_RESOURCE_KIND)
    path = item.data(0, ROLE_RESOURCE_PATH)
    key = (icon_size, revision, path if kind == "link" else None)
    if kind is None or item.data(0, _ROLE_ICON_KEY) == key:
        return
    if kind == "file":
        item.setIcon(0, file_icons.file_type_icon(
            path or item.text(0), size=icon_size,
            failed=bool(item.data(0, ROLE_RESOURCE_FAILED))))
    else:
        if kind == "link" and favicon_store is not None:
            cached = favicon_store.cached_icon_path(path)
            if cached and not QIcon(cached).isNull():
                item.setIcon(0, QIcon(cached))
                item.setData(0, _ROLE_ICON_KEY, key)
                return
        kind = "web" if kind == "link" else kind
        color = (theme.course_color(item.text(0))
                 if kind == "course" else None)
        item.setIcon(0, QIcon(artwork.kind_icon(
            kind, failed=bool(item.data(0, ROLE_RESOURCE_FAILED)),
            size=icon_size, color=color)))
    item.setData(0, _ROLE_ICON_KEY, key)


def refresh_course_icons(tree, favicon_store=None) -> None:
    """原地更新课程树全部图标, 不读索引、不检查磁盘."""
    icon_size = tree_icon_size()
    revision = theme.theme_revision()

    def update(item) -> None:
        _update_item_icon(item, icon_size, revision, favicon_store)
        for row in range(item.childCount()):
            update(item.child(row))

    tree.setUpdatesEnabled(False)
    try:
        for row in range(tree.topLevelItemCount()):
            update(tree.topLevelItem(row))
    finally:
        tree.setUpdatesEnabled(True)
    tree.viewport().update()


def request_course_favicons(tree, service) -> None:
    """登记课程树链接图标; 缓存立即回填, 未缓存按授权后台补齐."""
    def visit(item) -> None:
        if item.data(0, ROLE_RESOURCE_KIND) == "link":
            url = item.data(0, ROLE_RESOURCE_PATH)
            cached = service.store.cached_icon_path(url)
            if cached and not QIcon(cached).isNull():
                item.setIcon(0, QIcon(cached))
            service.request(url)
        for row in range(item.childCount()):
            visit(item.child(row))

    for row in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(row))


def update_course_favicon(tree, url: str, icon_path: str) -> None:
    """favicon 就绪后原地更新左栏对应链接, 不重建课程树."""
    icon = QIcon(icon_path)
    if icon.isNull():
        return

    def visit(item) -> None:
        if (item.data(0, ROLE_RESOURCE_KIND) == "link"
                and item.data(0, ROLE_RESOURCE_PATH) == url):
            item.setIcon(0, icon)
        for row in range(item.childCount()):
            visit(item.child(row))

    for row in range(tree.topLevelItemCount()):
        visit(tree.topLevelItem(row))
