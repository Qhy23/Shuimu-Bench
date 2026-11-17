"""左栏索引操作测试: 菜单形态, 新建/重命名/删除文件夹, 移动到, 登记落点."""

from PySide6.QtWidgets import QMessageBox

from shuimu.core.storage import Storage
from shuimu.gui.main_window import MainWindow


class _FakeInput:
    """替代 QInputDialog 的假件(返回固定 名称, ok).

    方法名须与 Qt 接口一致以便 monkeypatch 后被原样调用.
    """

    # pylint: disable=invalid-name
    def __init__(self, name):
        """记录希望 getText 返回的名称."""
        self._name = name

    def getText(self, *_args, **_kwargs):
        """返回 (构造时指定的名称, True)."""
        return self._name, True


class _FakeBox:
    """替代 QMessageBox 模块引用: 类形态同时服务静态 question 与实例构造.

    click_mode 控制非空文件夹弹框的点击: "remove"=第一个按钮,
    None=取消; question_answer 控制确认框返回值.
    """

    # pylint: disable=invalid-name
    StandardButton = QMessageBox.StandardButton
    Icon = QMessageBox.Icon
    ButtonRole = QMessageBox.ButtonRole
    question_answer = QMessageBox.StandardButton.Yes
    click_mode = None
    instances = []

    def __init__(self, *_a, **_k):
        """记录弹框按钮与正文(实例留档供测试断言)."""
        self._buttons = []
        self.text = ""
        type(self).instances.append(self)

    @staticmethod
    def question(*_a, **_k):
        """静态确认框: 返回类属性指定的按钮."""
        return _FakeBox.question_answer

    def setWindowTitle(self, _t):
        """忽略标题."""
    def setIcon(self, _i):
        """忽略图标."""
    def setText(self, text):
        """记录正文."""
        self.text = text
    def setInformativeText(self, _t):
        """忽略说明文字."""

    def addButton(self, text, _role):
        """登记并返回按钮占位."""
        button = (text,)
        self._buttons.append(button)
        return button

    def button(self, _sb):
        """无标准按钮."""
        return None

    def setDefaultButton(self, _b):
        """忽略默认按钮."""

    def exec(self):
        """立即返回(点击由 click_mode 决定)."""
        return 0

    def clickedButton(self):
        """按 click_mode 返回被点击按钮, 取消时 None."""
        if _FakeBox.click_mode == "remove" and self._buttons:
            return self._buttons[0]
        return None


class _FakePicker:
    """替代 FolderPickerDialog: exec 返回 True, 选中指定文件夹."""

    def __init__(self, selected):
        """记录选中的文件夹 id."""
        self._selected = selected

    def exec(self):
        """直接视为确认."""
        return True

    def selected_folder_id(self):
        """返回构造时指定的文件夹 id."""
        return self._selected


def _texts(menu):
    """菜单动作文字列表."""
    return [act.text() for act in menu.actions()]


def _setup(qtbot, tmp_path):
    """课件/第1周 两级文件夹 + 文件与链接各一条入库, 返回全套引用."""
    storage = Storage(str(tmp_path / "ops.db"))
    window = MainWindow(storage, schedule_path=str(tmp_path / "s.yaml"),
                        links_path=str(tmp_path / "l.yaml"))
    qtbot.addWidget(window)
    cid = storage.ensure_default_course()
    f1 = storage.add_folder(cid, None, "课件")
    f2 = storage.add_folder(cid, f1, "第1周")
    f = tmp_path / "a.txt"
    f.write_text("x")
    rid = storage.add_resource(cid, "file", str(f), folder_id=f2)
    storage.add_resource(cid, "link", "https://example.com/", folder_id=f1)
    window.reload_courses()
    course = window.course_tree.topLevelItem(0)
    return window, storage, cid, f1, f2, rid, course


class TestMenus:
    """菜单形态随节点类型变化."""

    def test_menu_on_course_folder_file_link(self, qtbot, tmp_path):
        """四类节点菜单条目各按设计."""
        window, _s, _c, _f1, _f2, _rid, course = _setup(qtbot, tmp_path)
        assert _texts(window._build_course_menu(course)) \
            == ["新建目录", "添加链接", "", "删除课程"]
        folder_node = course.child(0)
        assert _texts(window._build_course_menu(folder_node))[:2] \
            == ["新建子目录", ""]
        file_node = folder_node.child(0).child(0)
        assert _texts(window._build_course_menu(file_node)) \
            == ["打开", "在中栏定位", "", "移动到...", "移除索引"]
        link_node = folder_node.child(1)
        assert _texts(window._build_course_menu(link_node)) \
            == ["打开", "", "移动到...", "移除索引"]
        assert _texts(window._build_course_menu(None)) == []
        _s.close()

    def test_shortcuts_registered(self, qtbot, tmp_path):
        """仅 F2/Del 两个快捷键(索引操作, 无文件操作键)."""
        window, _s, *_ = _setup(qtbot, tmp_path)
        acts = {act.shortcut().toString()
                for act in window.course_tree.actions()
                if not act.shortcut().isEmpty()}
        assert acts == {"F2", "Del"}
        _s.close()


class TestFolderOps:
    """文件夹新建/重命名/删除."""

    def test_folder_create_under_selection(self, qtbot, tmp_path, monkeypatch):
        """选中文件夹内新建子目录, 父链展开可见."""
        window, storage, cid, f1, _f2, _rid, _course = _setup(qtbot, tmp_path)
        window.course_tree.setCurrentItem(
            window.course_tree.topLevelItem(0).child(0))
        monkeypatch.setattr("shuimu.gui.main_window.QInputDialog",
                            _FakeInput("作业"))
        window._folder_create()
        sub = next(row for row in storage.list_folders(cid)
                   if row["name"] == "作业")
        assert sub["parent_id"] == f1
        course = window.course_tree.topLevelItem(0)   # 重建后重取节点
        folder_node = course.child(0)
        assert folder_node.text(0) == "课件"
        assert folder_node.isExpanded()               # 父链已展开
        storage.close()

    def test_rename_folder_only(self, qtbot, tmp_path, monkeypatch):
        """F2 重命名文件夹; 索引条目上 F2 给提示."""
        window, storage, _cid, _f1, _f2, _rid, _course = _setup(qtbot, tmp_path)
        window.course_tree.setCurrentItem(
            window.course_tree.topLevelItem(0).child(0))
        monkeypatch.setattr("shuimu.gui.main_window.QInputDialog",
                            _FakeInput("讲义"))
        window._rename_current()
        names = {row["name"] for row in storage.list_folders(
            storage.ensure_default_course())}
        assert "讲义" in names and "课件" not in names
        # 索引条目上重命名给提示
        course = window.course_tree.topLevelItem(0)
        window.course_tree.setCurrentItem(course.child(0).child(0).child(0))
        window._rename_current()
        assert "文件夹" in window.statusBar().currentMessage()
        storage.close()

    def test_delete_empty_folder(self, qtbot, tmp_path, monkeypatch):
        """空文件夹删除走确认."""
        window, storage, cid, *_ = _setup(qtbot, tmp_path)
        f3 = storage.add_folder(cid, None, "临时")
        window.reload_courses()
        course = window.course_tree.topLevelItem(0)
        empty = next(course.child(i) for i in range(course.childCount())
                     if course.child(i).text(0) == "临时")
        window.course_tree.setCurrentItem(empty)
        monkeypatch.setattr("shuimu.gui.main_window.QMessageBox", _FakeBox)
        window._delete_current()
        assert storage.get_folder(f3) is None
        storage.close()

    def test_delete_dialog_counts_descendants(self, qtbot, tmp_path,
                                              monkeypatch):
        """删除弹框统计全部子孙(索引条目与子文件夹), 非直接子项."""
        window, storage, _cid, _f1, _f2, _rid, _course = _setup(qtbot, tmp_path)
        monkeypatch.setattr("shuimu.gui.main_window.QMessageBox", _FakeBox)
        _FakeBox.instances.clear()
        window.course_tree.setCurrentItem(
            window.course_tree.topLevelItem(0).child(0))     # 课件
        _FakeBox.click_mode = None                           # 取消, 只看弹框
        window._delete_current()
        text = _FakeBox.instances[-1].text
        # 课件 下: 链接(直属于课件) + 第1周/a.txt = 2 条索引, 1 个子文件夹
        assert "2 个索引条目" in text and "1 个子文件夹" in text
        _FakeBox.click_mode = None
        storage.close()

    def test_delete_nonempty_cascade_and_cancel(self, qtbot, tmp_path,
                                                monkeypatch):
        """非空弹框: 一键全部移除并删除(仅索引); 取消则原样保留."""
        window, storage, _cid, f1, _f2, rid, _course = _setup(qtbot, tmp_path)
        monkeypatch.setattr("shuimu.gui.main_window.QMessageBox", _FakeBox)
        window.course_tree.setCurrentItem(
            window.course_tree.topLevelItem(0).child(0))
        _FakeBox.click_mode = None                     # 取消分支
        window._delete_current()
        assert storage.get_folder(f1) is not None
        assert storage.get_resource(rid) is not None
        _FakeBox.click_mode = "remove"                 # 全部移除并删除
        window._delete_current()
        assert storage.get_folder(f1) is None
        assert storage.get_resource(rid) is None
        assert (tmp_path / "a.txt").exists()           # 磁盘不动
        _FakeBox.click_mode = None
        storage.close()


class TestMoveAndRegister:
    """移动到与登记落点."""

    def test_move_resource_via_picker(self, qtbot, tmp_path, monkeypatch):
        """索引条目经选择框移动到目标文件夹."""
        window, storage, _cid, f1, _f2, rid, course = _setup(qtbot, tmp_path)
        file_node = course.child(0).child(0).child(0)
        window.course_tree.setCurrentItem(file_node)
        monkeypatch.setattr("shuimu.gui.main_window.dialogs.FolderPickerDialog",
                            lambda *a, **k: _FakePicker(f1))
        window._move_current()
        assert storage.get_resource(rid)["folder_id"] == f1
        storage.close()

    def test_move_folder_excludes_descendants(self, qtbot, tmp_path,
                                              monkeypatch):
        """移动文件夹时选择框排除自身与子孙(排除集传入)."""
        window, storage, _cid, f1, f2, _rid, course = _setup(qtbot, tmp_path)
        seen = {}

        def fake_picker(_folders, _name, _parent=None, exclude_ids=None):
            seen["exclude"] = exclude_ids
            return _FakePicker(None)

        monkeypatch.setattr(
            "shuimu.gui.main_window.dialogs.FolderPickerDialog", fake_picker)
        window.course_tree.setCurrentItem(course.child(0).child(0))  # 第1周
        window._move_current()
        assert seen["exclude"] == {f2}                # 只排除自身与子孙
        assert storage.get_folder(f2)["parent_id"] is None
        assert storage.get_folder(f1) is not None
        storage.close()

    def test_register_lands_in_selected_folder(self, qtbot, tmp_path):
        """中栏登记落点跟随左栏选中的文件夹."""
        window, storage, _cid, f1, _f2, _rid, course = _setup(qtbot, tmp_path)
        window.course_tree.setCurrentItem(course.child(0))
        new_file = tmp_path / "b.txt"
        new_file.write_text("y")
        window._on_add_requested(str(new_file))
        row = storage.find_resource(storage.ensure_default_course(),
                                    str(new_file))
        assert row is not None and row["folder_id"] == f1
        # 选中的不是文件夹(课程节点)时落课程根
        window.course_tree.setCurrentItem(
            window.course_tree.topLevelItem(0))
        third = tmp_path / "c.txt"
        third.write_text("z")
        window._on_add_requested(str(third))
        row = storage.find_resource(storage.ensure_default_course(), str(third))
        assert row is not None and row["folder_id"] is None
        storage.close()

    def test_remove_index(self, qtbot, tmp_path, monkeypatch):
        """移除索引走确认, 只动索引行."""
        window, storage, _cid, _f1, _f2, rid, course = _setup(qtbot, tmp_path)
        file_node = course.child(0).child(0).child(0)
        window.course_tree.setCurrentItem(file_node)
        monkeypatch.setattr("shuimu.gui.main_window.QMessageBox", _FakeBox())
        window._remove_selected()
        assert storage.get_resource(rid) is None
        assert (tmp_path / "a.txt").exists()
        storage.close()
