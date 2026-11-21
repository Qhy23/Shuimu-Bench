"""课程树测试: 虚拟文件夹层级组装, 展开记忆, 定位与移除提示."""

import os

from shuimu.gui.main_window import MainWindow
from shuimu.core.storage import Storage


def _make_window(qtbot, tmp_path):
    storage = Storage(str(tmp_path / "tree.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "l.yaml"))
    qtbot.addWidget(window)
    return storage, window


def _setup(storage, tmp_path):
    """课件/第1周 两级文件夹 + 链接与文件各一条入库; 返回 (课程id, f1, f2)."""
    cid = storage.ensure_default_course()
    f1 = storage.add_folder(cid, None, "课件")
    f2 = storage.add_folder(cid, f1, "第1周")
    storage.add_resource(cid, "link", "https://example.com/", folder_id=f1)
    missing = tmp_path / "不存在的.pdf"
    storage.add_resource(cid, "file", str(missing), folder_id=None)
    return cid, f1, f2


class TestFolderTree:
    """文件夹在前 + 索引条目在后的层级组装."""

    def test_assembly(self, qtbot, tmp_path):
        """文件夹递归在前, 资源按归属挂载, 失效标灰在标签上."""
        storage, window = _make_window(qtbot, tmp_path)
        _setup(storage, tmp_path)
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        assert [course.child(i).text(0)
                for i in range(course.childCount())] == ["课件", "不存在的.pdf (失效)"]
        f1_node = course.child(0)
        assert [f1_node.child(i).text(0)
                for i in range(f1_node.childCount())] \
            == ["第1周", "https://example.com/"]

    def test_legacy_dir_resources_hidden(self, qtbot, tmp_path):
        """历史目录索引行不再显示(目录索引概念已废)."""
        storage, window = _make_window(qtbot, tmp_path)
        cid = storage.ensure_default_course()
        sub = tmp_path / "资料"
        sub.mkdir()
        storage.add_resource(cid, "dir", str(sub))
        f = tmp_path / "a.txt"
        f.write_text("x")
        storage.add_resource(cid, "file", str(f))
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        assert course.childCount() == 1                 # 只剩文件条目
        assert course.child(0).text(0) == "a.txt"
        storage.close()

    def test_locate_file_selects_in_browser(self, qtbot, tmp_path):
        """文件索引定位: 中栏切到父目录并选中该文件."""
        storage, window = _make_window(qtbot, tmp_path)
        cid = storage.ensure_default_course()
        f = tmp_path / "a.txt"
        f.write_text("x")
        rid = storage.add_resource(cid, "file", str(f))
        window.reload_courses()
        window._locate_resource(storage.get_resource(rid))
        assert os.path.realpath(window.workspace_root) \
            == os.path.realpath(str(tmp_path))
        assert window.file_page.file_view.currentIndex().data() \
            == f.name or window.file_page.selected_path() == str(f)
        storage.close()

    def test_remove_selected_folder_hint(self, qtbot, tmp_path):
        """文件夹上移除选中给提示, 不误删."""
        storage, window = _make_window(qtbot, tmp_path)
        cid, f1, _f2 = _setup(storage, tmp_path)
        window.reload_courses()
        folder_node = window.course_tree.topLevelItem(0).child(0)
        window.course_tree.setCurrentItem(folder_node)
        window._remove_selected()
        assert "文件夹" in window.statusBar().currentMessage()
        assert storage.get_folder(f1) is not None
        assert storage.list_resources(cid)
        storage.close()

    def test_expansion_default_collapsed_and_remembered(self, qtbot, tmp_path):
        """默认全收起; 展开的课程与文件夹重建后保持(会话内记忆)."""
        storage, window = _make_window(qtbot, tmp_path)
        _cid, _f1, _f2 = _setup(storage, tmp_path)
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        assert not course.isExpanded()
        course.setExpanded(True)
        folder_node = course.child(0)
        folder_node.setExpanded(True)
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        assert course.isExpanded() and course.child(0).isExpanded()
        course.child(0).setExpanded(False)
        window.reload_courses()
        assert not window.course_tree.topLevelItem(0).child(0).isExpanded()
        storage.close()
