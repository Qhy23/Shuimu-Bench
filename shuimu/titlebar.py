"""自定义无边框标题栏: 拖动移动, 最小化/最大化/关闭按钮.

替代系统窗口框架. 样式引用 theme 的设计 token.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

from . import artwork, theme

_BUTTON_STYLE = f"""
QPushButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    color: {theme.FG_DIM};
    padding: 0;
}}
QPushButton:hover {{
    background-color: {theme.BG_HOVER};
    color: {theme.FG_TEXT};
}}
QPushButton#close:hover {{
    background-color: #e81123;
    color: white;
}}
"""


class TitleBar(QWidget):
    """无边框窗口的标题栏."""

    HEIGHT = 36

    def __init__(self, window, title: str, on_about=None):
        super().__init__(window)
        self._window = window
        self._drag_pos = None
        self.setFixedHeight(self.HEIGHT)
        self.setStyleSheet(
            _BUTTON_STYLE
            + f"QWidget{{background-color:{theme.GRADIENT_TITLEBAR};"
              f"border-top-left-radius:{theme.RADIUS};"
              f"border-top-right-radius:{theme.RADIUS};}}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(2)

        self.logo = QLabel(self)
        self.logo.setPixmap(artwork.bench_icon(self._logo_size_px()))
        logo = self.logo
        layout.addWidget(logo)
        label = QLabel(title, self)
        label.setObjectName("apptitle")
        layout.addWidget(label)
        layout.addItem(QSpacerItem(0, 0, QSizePolicy.Expanding,
                                   QSizePolicy.Minimum))

        if on_about is not None:
            about_btn = self._make_button("?", on_about)
            about_btn.setFixedSize(32, self.HEIGHT - 8)
            layout.addWidget(about_btn)

        self.btn_min = self._make_button("—", self._window.showMinimized)
        self.btn_max = self._make_button("▢", self._toggle_max_restore)
        self.btn_close = self._make_button("✕", self._window.close,
                                           object_name="close")
        for btn in (self.btn_min, self.btn_max, self.btn_close):
            btn.setFixedSize(40, self.HEIGHT - 8)
            layout.addWidget(btn)

    def _logo_size_px(self) -> int:
        """logo 边长随全局字号(夹取 16-34)."""
        return int(max(16, min(34, round(theme.current_font_px() * 1.7))))

    def set_font_px(self, _font_px: int) -> None:
        """随全局字号更新 logo 尺寸(尺寸读 theme 当前值, 参数保持接口一致)."""
        self.logo.setPixmap(artwork.bench_icon(self._logo_size_px()))

    def _make_button(self, text: str, slot, object_name: str = "") -> QPushButton:
        """创建标题栏按钮."""
        btn = QPushButton(text, self)
        if object_name:
            btn.setObjectName(object_name)
        btn.clicked.connect(slot)
        return btn

    def _toggle_max_restore(self) -> None:
        """最大化/还原切换."""
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

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
