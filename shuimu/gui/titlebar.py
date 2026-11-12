"""自定义无边框标题栏: 拖动移动, 图标化窗口控制与工具按钮.

替代系统窗口框架. 样式引用 theme 当前 token; 窗口控制按钮用
artwork.window_icon 自绘图标(替代文本字符), 最大化/还原随窗口状态
切换图标; 设置(齿轮)按钮经 on_settings 回调弹出主题菜单.
"""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

from shuimu.gui import artwork, theme

_BUTTON_STYLE = """
QPushButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0;
}}
QPushButton:hover {{
    background-color: {hover};
}}
QPushButton#close:hover {{
    background-color: #e81123;
}}
"""


class TitleBar(QWidget):
    """无边框窗口的标题栏."""

    HEIGHT = 36

    def __init__(self, window, title: str, on_about=None, on_log=None,
                 on_settings=None):
        super().__init__(window)
        self._window = window
        self._drag_pos = None
        self.setFixedHeight(self.HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(2)

        self.logo = QLabel(self)
        self.logo.setPixmap(artwork.bench_icon(self._logo_size_px()))
        layout.addWidget(self.logo)
        label = QLabel(title, self)
        label.setObjectName("apptitle")
        layout.addWidget(label)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding,
                                   QSizePolicy.Minimum))

        button_size = (40, self.HEIGHT - 8)
        self._log_btn = None
        self._settings_btn = None
        self._about_btn = None
        if on_log is not None:
            self._log_btn = self._make_button("", on_log,
                                              object_name="log")
            self._log_btn.setIcon(QIcon(artwork.log_icon(16)))
            self._log_btn.setIconSize(QSize(16, 16))
            self._log_btn.setToolTip("操作留痕")
            self._log_btn.setFixedSize(32, self.HEIGHT - 8)
            layout.addWidget(self._log_btn)
        if on_settings is not None:
            self._settings_btn = self._make_button(
                "", lambda: on_settings(self._settings_btn),
                object_name="settings")
            self._settings_btn.setIcon(QIcon(artwork.settings_icon(16)))
            self._settings_btn.setIconSize(QSize(16, 16))
            self._settings_btn.setToolTip("设置与主题")
            self._settings_btn.setFixedSize(32, self.HEIGHT - 8)
            layout.addWidget(self._settings_btn)
        if on_about is not None:
            self._about_btn = self._make_button("", on_about,
                                                object_name="about")
            self._about_btn.setIcon(QIcon(artwork.about_icon(14)))
            self._about_btn.setIconSize(QSize(14, 14))
            self._about_btn.setToolTip("关于")
            self._about_btn.setFixedSize(32, self.HEIGHT - 8)
            layout.addWidget(self._about_btn)

        self.btn_min = self._make_button("", self._window.showMinimized,
                                         icon=artwork.window_icon("min"))
        self.btn_max = self._make_button("", self._toggle_max_restore,
                                         icon=artwork.window_icon("max"))
        self.btn_close = self._make_button("", self._window.close,
                                           object_name="close",
                                           icon=artwork.window_icon("close"))
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            btn.setFixedSize(*button_size)
            btn.setIconSize(QSize(12, 12))
            layout.addWidget(btn)
        self._sync_max_icon(self._window.windowState())
        self._window.installEventFilter(self)
        self._restyle()

    def _restyle(self) -> None:
        """按当前主题重刷背景与按钮悬停色."""
        tk = theme.tokens()
        self.setStyleSheet(
            _BUTTON_STYLE.format(hover=tk.hover)
            + f"QWidget{{background-color:{tk.elevated};"
              f"border-bottom: 1px solid {tk.border};}}")

    def set_font_px(self, _font_px: int) -> None:
        """随全局字号更新 logo 尺寸(尺寸读 theme 当前值, 参数保持接口一致)."""
        self.logo.setPixmap(artwork.bench_icon(self._logo_size_px()))

    def refresh_theme(self) -> None:
        """主题切换后重建图标与配色."""
        self._restyle()
        self.logo.setPixmap(artwork.bench_icon(self._logo_size_px()))
        if self._settings_btn is not None:
            self._settings_btn.setIcon(QIcon(artwork.settings_icon(16)))
        if self._about_btn is not None:
            self._about_btn.setIcon(QIcon(artwork.about_icon(14)))
        if self._log_btn is not None:
            self._log_btn.setIcon(QIcon(artwork.log_icon(16)))
        self._sync_max_icon(self._window.windowState())

    def _logo_size_px(self) -> int:
        """logo 边长随全局字号(夹取 16-34)."""
        return int(max(16, min(34, round(theme.current_font_px() * 1.7))))

    def _make_button(self, text: str, slot, object_name: str = "",
                     icon=None) -> QPushButton:
        """创建标题栏按钮."""
        btn = QPushButton(text, self)
        if object_name:
            btn.setObjectName(object_name)
        if icon is not None:
            btn.setIcon(icon)
        btn.clicked.connect(slot)
        return btn

    def _toggle_max_restore(self) -> None:
        """最大化/还原切换."""
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def _sync_max_icon(self, state) -> None:
        """最大化状态切换 max/restore 图标."""
        maximized = bool(state & Qt.WindowState.WindowMaximized)
        self.btn_max.setIcon(QIcon(
            artwork.window_icon("restore" if maximized else "max")))
        self.btn_max.setToolTip("还原" if maximized else "最大化")

    def eventFilter(self, obj, event):
        """监听窗口状态变化以切换最大化/还原图标."""
        if obj is self._window and \
                event.type() == QEvent.Type.WindowStateChange:
            self._sync_max_icon(self._window.windowState())
        return super().eventFilter(obj, event)

    # ---- 拖动 ----

    def mousePressEvent(self, event) -> None:
        """按下记录起点(用于拖动与双击判断)."""
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - \
                self._window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """按住左键拖动窗口; 最大化时拖动先还原."""
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            if self._window.isMaximized():
                self._window.showNormal()
                self._drag_pos = None
                return
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """松开结束拖动."""
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """双击标题栏切换最大化/还原."""
        if event.button() == Qt.LeftButton:
            self._toggle_max_restore()
        super().mouseDoubleClickEvent(event)
