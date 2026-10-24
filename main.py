"""水木工作台(ShuimuBench) -- 应用入口."""

import sys

from PySide6.QtWidgets import QApplication

from shuimu.app import MainWindow
from shuimu.config import font_size_value
from shuimu.paths import default_db_path
from shuimu.storage import Storage
from shuimu.theme import apply_theme

def main() -> int:
    """初始化存储与主窗口, 进入事件循环."""
    app = QApplication(sys.argv)
    apply_theme(app)
    storage = Storage(default_db_path())
    window = MainWindow(storage)
    cfg = window.ctl.config["app"]
    window.set_app_font(font_size_value(window.ctl.config), quiet=True)
    window.setWindowTitle(cfg["title"])
    window.resize(*cfg["window_size"])
    window.show()
    code = app.exec()
    storage.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
