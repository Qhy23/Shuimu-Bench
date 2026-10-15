"""GUI 冒烟测试: 主窗口三栏结构. 详细交互由人工演示核对."""

from shuimu.app import MainWindow
from shuimu.storage import Storage


def test_main_window_structure(qtbot, tmp_path):
    """窗口有三栏, 默认课程已建立, 日志页可读."""
    storage = Storage(str(tmp_path / "ui.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    assert window.splitter.count() == 3
    assert window.course_tree.topLevelItemCount() == 1  # 默认课程"未分组"
    assert window.tabs.count() == 2
    assert "暂无操作记录" in window.log_view.toPlainText()
    storage.close()


def test_add_link_via_storage_shows_in_tree(qtbot, tmp_path):
    """登记链接资源后, 课程树出现对应节点."""
    storage = Storage(str(tmp_path / "ui2.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    storage.add_resource(course_id, "link", "https://learn.tsinghua.edu.cn")
    window.reload_courses()
    course_node = window.course_tree.topLevelItem(0)
    assert course_node.childCount() == 1
    assert "[链接]" in course_node.child(0).text(0)
    storage.close()
