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


def test_add_link_via_controller_shows_in_tree(qtbot, tmp_path):
    """经 controller 登记链接后, 课程树出现对应节点."""
    storage = Storage(str(tmp_path / "ui2.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    course_id = storage.list_courses()[0]["id"]
    window.ctl.add_resource(course_id, "https://learn.tsinghua.edu.cn")
    window.reload_courses()
    course_node = window.course_tree.topLevelItem(0)
    assert course_node.childCount() == 1
    assert "[链接]" in course_node.child(0).text(0)
    storage.close()


def test_workspace_root_switch(qtbot, tmp_path, monkeypatch):
    """中栏工作目录可切换, 非法目录弹警告框且保持当前状态."""
    warnings = []
    monkeypatch.setattr("shuimu.app._warn",
                        lambda _p, title, text: warnings.append((title, text)))
    storage = Storage(str(tmp_path / "ui3.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    sub = tmp_path / "sub"
    sub.mkdir()
    original = window.workspace_root
    assert window.set_workspace_root(str(sub))
    assert window.workspace_root == str(sub)
    assert window.root_edit.text() == str(sub)
    # 非法目录: 弹警告框, 拒绝并回退路径栏
    assert not window.set_workspace_root(str(tmp_path / "nonexistent"))
    assert len(warnings) == 1 and "无效的工作目录" in warnings[0][0]
    assert window.workspace_root == str(sub)
    assert window.root_edit.text() == str(sub)
    assert original != str(sub)
    storage.close()


def test_preview_bad_dir_warns(qtbot, tmp_path, monkeypatch):
    """整理页输入非法目录时弹警告框."""
    warnings = []
    monkeypatch.setattr("shuimu.app._warn",
                        lambda _p, title, text: warnings.append((title, text)))
    storage = Storage(str(tmp_path / "ui4.db"))
    window = MainWindow(storage)
    qtbot.addWidget(window)
    window.dir_edit.setText(str(tmp_path / "nonexistent"))
    window._preview_plan()
    assert len(warnings) == 1 and "无法生成预览" in warnings[0][0]
    storage.close()
