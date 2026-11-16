"""课程树测试: 资源按目录层级嵌套显示(虚拟节点/单链折叠/展开记忆)."""

import os

from shuimu.gui.main_window import (
    MainWindow,
    ROLE_DIR_PATH,
    _display_forest,
)
from shuimu.core.storage import Storage


def _res(kind, path, rid, name=None):
    """构造 _display_forest 可消费的资源行(dict 代 sqlite Row)."""
    return {"id": rid, "kind": kind, "path_or_url": path,
            "display_name": name or os.path.basename(path)}


class TestDisplayForest:
    """纯数据组装: 嵌套/折叠/平铺顺序."""

    def test_lone_file_collapses_to_resource(self):
        """单链上的孤立文件直接以资源节点出现, 不产生虚拟目录."""
        forest = _display_forest([_res("file", "/a/b/f.txt", 1)])
        assert len(forest) == 1
        assert forest[0]["kind"] == "resource"
        assert forest[0]["resource"]["id"] == 1

    def test_same_dir_files_share_virtual_parent(self):
        """同目录两个文件共享一个虚拟目录节点(记录完整路径)."""
        forest = _display_forest([_res("file", "/a/ws/f1.pdf", 1),
                                  _res("file", "/a/ws/f2.pdf", 2)])
        assert len(forest) == 1
        group = forest[0]
        assert group["kind"] == "dir" and group["path"] == "/a/ws"
        assert [c["resource"]["id"] for c in group["children"]] == [1, 2]

    def test_single_chain_below_branch_folds(self):
        """分叉点之下不再分叉的单链虚拟目录折叠(文件直挂分叉节点)."""
        forest = _display_forest([_res("file", "/a/ws/f1.pdf", 1),
                                  _res("file", "/a/ws/课件/b.pdf", 2)])
        group = forest[0]
        assert group["path"] == "/a/ws"          # 分叉在 ws
        assert [c["resource"]["id"] for c in group["children"]] == [1, 2]

    def test_dir_resource_anchor_with_children(self):
        """目录资源本身是节点, 其下资源挂为子节点."""
        forest = _display_forest([_res("dir", "/a/ws", 1, "ws"),
                                  _res("file", "/a/ws/f1.pdf", 2),
                                  _res("file", "/a/ws/sub/f2.pdf", 3)])
        assert len(forest) == 1
        node = forest[0]
        assert node["kind"] == "resource" and node["resource"]["id"] == 1
        assert [c["resource"]["id"] for c in node["children"]] == [2, 3]

    def test_links_interleave_by_added_order(self):
        """链接平铺, 与文件组按加入顺序交错."""
        forest = _display_forest([_res("link", "https://x/", 1, "x"),
                                  _res("file", "/a/f1.pdf", 2),
                                  _res("link", "https://y/", 3, "y"),
                                  _res("file", "/a/f2.pdf", 4)])
        assert [n["resource"]["id"] if n["kind"] == "resource"
                else n["path"] for n in forest] == \
            [1, "/a", 3]
        assert [c["resource"]["id"] for c in forest[1]["children"]] == [2, 4]

    def test_two_roots_two_groups(self):
        """不同根路径各自成组; 孤立单链文件不产生虚拟目录."""
        forest = _display_forest([_res("file", "/a/f1.pdf", 1),
                                  _res("file", "/a/sub/f2.pdf", 2),
                                  _res("file", "/b/f3.pdf", 3)])
        assert [(n["kind"], n.get("path") or n["resource"]["id"])
                for n in forest] == [("dir", "/a"), ("resource", 3)]
        assert [c["resource"]["id"] for c in forest[0]["children"]] == [1, 2]


def _make_window(qtbot, tmp_path):
    storage = Storage(str(tmp_path / "tree.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "l.yaml"))
    qtbot.addWidget(window)
    return storage, window


class TestCourseTreeGui:
    """GUI 树形结构与交互."""

    def test_nested_virtual_nodes(self, qtbot, tmp_path):
        """同目录资源挂到虚拟节点下: 标签取目录名, 提示为完整路径."""
        storage, window = _make_window(qtbot, tmp_path)
        course_id = storage.list_courses()[0]["id"]
        ws = tmp_path / "ws"
        (ws / "课件").mkdir(parents=True)
        for rel in ("README.md", "课件/第7讲.pdf"):
            f = ws / rel
            f.write_text("x")
            window.ctl.add_resource(course_id, str(f))
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        # tmp_path 单链折叠到分叉点 ws: 顶层组 = ws(虚拟, basename + 完整路径提示)
        top = course.child(0)
        assert top.text(0) == "ws"
        assert top.toolTip(0) == str(ws)
        assert top.data(0, ROLE_DIR_PATH) == str(ws)
        # ws 其下两个文件, 课件一级单链折叠
        assert [top.child(i).text(0) for i in range(top.childCount())] \
            == ["README.md", "第7讲.pdf"]
        storage.close()

    def test_virtual_node_double_click_locates(self, qtbot, tmp_path):
        """双击虚拟目录节点在中栏定位."""
        storage, window = _make_window(qtbot, tmp_path)
        course_id = storage.list_courses()[0]["id"]
        sub = tmp_path / "资料"
        sub.mkdir()
        for name in ("a.pdf", "b.pdf"):  # 两个文件让"资料"成为分叉虚拟节点
            (sub / name).write_text("x")
            window.ctl.add_resource(course_id, str(sub / name))
        window.reload_courses()
        virtual = window.course_tree.topLevelItem(0).child(0)
        assert virtual.data(0, ROLE_DIR_PATH) == str(sub)
        window._on_resource_activated(virtual, 0)
        assert os.path.realpath(window.workspace_root) \
            == os.path.realpath(str(sub))
        # 目录消失后提示失效且不切目录
        virtual.setData(0, ROLE_DIR_PATH, str(tmp_path / "不存在"))
        window._on_resource_activated(virtual, 0)
        assert "已失效" in window.statusBar().currentMessage()
        assert os.path.realpath(window.workspace_root) \
            == os.path.realpath(str(sub))
        storage.close()

    def test_remove_selected_on_virtual_node_hint(self, qtbot, tmp_path):
        """虚拟节点上移除选中给提示, 不弹确认."""
        storage, window = _make_window(qtbot, tmp_path)
        course_id = storage.list_courses()[0]["id"]
        for rel in ("d1/a.pdf", "d2/b.pdf"):  # 两分支让 tmp_path 成虚拟节点
            f = tmp_path / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")
            window.ctl.add_resource(course_id, str(f))
        window.reload_courses()
        virtual = window.course_tree.topLevelItem(0).child(0)
        assert virtual.data(0, ROLE_DIR_PATH) == str(tmp_path)
        window.course_tree.setCurrentItem(virtual)
        window._remove_selected()
        assert "目录层级节点" in window.statusBar().currentMessage()
        assert storage.list_resources(course_id)  # 资源未被误删
        storage.close()

    def test_expansion_default_collapsed_and_remembered(self, qtbot, tmp_path):
        """默认全收起; 手动展开后重建树仍保持(会话内记忆)."""
        storage, window = _make_window(qtbot, tmp_path)
        course_id = storage.list_courses()[0]["id"]
        (tmp_path / "ws").mkdir()
        (tmp_path / "ws" / "a.pdf").write_text("x")
        window.ctl.add_resource(course_id, str(tmp_path / "ws" / "a.pdf"))
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        assert not course.isExpanded()           # 默认全不展开
        course.setExpanded(True)                 # 信号驱动记忆
        virtual = course.child(0)
        virtual.setExpanded(True)
        window.reload_courses()                  # 增删资源后重建
        course = window.course_tree.topLevelItem(0)
        assert course.isExpanded() and course.child(0).isExpanded()
        course.child(0).setExpanded(False)       # 收起后重建仍收起
        window.reload_courses()
        assert not window.course_tree.topLevelItem(0).child(0).isExpanded()
        storage.close()
