"""水木工作台(ShuimuBench) -- 应用入口."""

import sys

from PySide6.QtWidgets import QApplication

from shuimu.app import MainWindow
from shuimu.paths import default_db_path
from shuimu.storage import Storage

APP_TITLE = "水木工作台 ShuimuBench"


def main() -> int:
    """初始化存储与主窗口, 进入事件循环."""
    app = QApplication(sys.argv)
    storage = Storage(default_db_path())
    window = MainWindow(storage)
    window.setWindowTitle(APP_TITLE)
    window.resize(1200, 720)
    window.show()
    code = app.exec()
    storage.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
