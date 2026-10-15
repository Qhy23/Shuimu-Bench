"""水木工作台(ShuimuBench)——三栏主窗口骨架。

布局:左栏课程空间树(占位) | 中栏文件浏览 | 右栏面板(结果/日志)。
首次 AI 编程实践(CP1):验证 PySide6 环境与基本布局。
"""

import sys

from PySide6.QtCore import QDir
from PySide6.QtWidgets import (
    QApplication,
    QFileSystemModel,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QPlainTextEdit,
)

APP_TITLE = "水木工作台 ShuimuBench"


def build_course_tree(parent) -> QTreeWidget:
    """左栏:课程空间树(占位数据,后续接 SQLite 索引)。"""
    tree = QTreeWidget(parent)
    tree.setHeaderLabel("课程空间")
    for course in ("高等数学", "大学物理", "程序设计"):
        node = QTreeWidgetItem(tree, [course])
        QTreeWidgetItem(node, ["课件"])
        QTreeWidgetItem(node, ["作业"])
    return tree


def build_file_browser(parent) -> QTreeView:
    """中栏:文件资源管理器,直读真实磁盘。"""
    view = QTreeView(parent)
    model = QFileSystemModel(view)
    model.setRootPath(QDir.homePath())
    view.setModel(model)
    view.setRootIndex(model.index(QDir.homePath()))
    view.setColumnWidth(0, 260)
    return view


def build_panel(parent) -> QTabWidget:
    """右栏:结果/日志面板(占位空页)。"""
    tabs = QTabWidget(parent)
    tabs.addTab(QPlainTextEdit(plainText="(结果输出)", readOnly=True), "结果")
    tabs.addTab(QPlainTextEdit(plainText="(操作日志)", readOnly=True), "日志")
    return tabs


def main() -> int:
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle(APP_TITLE)
    window.resize(1100, 700)

    splitter = QSplitter(window)
    splitter.addWidget(build_course_tree(splitter))
    splitter.addWidget(build_file_browser(splitter))
    splitter.addWidget(build_panel(splitter))
    splitter.setSizes([200, 560, 340])
    window.setCentralWidget(splitter)

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
