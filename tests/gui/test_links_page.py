"""links_page.py 测试: 分组树渲染/选中上下文/打开与 LinkDialog 校验."""

import pytest
from PySide6.QtWidgets import QDialog

from shuimu.core import url_open
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage
from shuimu.gui import theme
from shuimu.gui.links_page import ROLE_LINK, LinkDialog, LinksPage

GROUPED_YAML = (
    "links:\n"
    "  - name: 学堂\n"
    "    url: https://learn.example.com\n"
    "    group: 信息化服务\n"
    "  - name: 门户\n"
    "    url: https://info.example.com\n"
    "    group: 信息门户\n"
    "  - name: 散链\n"
    "    url: https://misc.example.com\n"
)


@pytest.fixture()
def ctl(tmp_path):
    """独立 controller: 预置两组一条未分组的临时数据文件."""
    links_path = tmp_path / "links.yaml"
    links_path.write_text(GROUPED_YAML, encoding="utf-8")
    storage = Storage(str(tmp_path / "lp.db"))
    yield Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(links_path))
    storage.close()


@pytest.fixture(autouse=True)
def fake_opener(monkeypatch):
    """全部测试注入假默认 opener, 不真开浏览器."""
    opened = []
    monkeypatch.setattr(url_open, "_state",
                        {"default": lambda url: opened.append(url) or True,
                         "registry": {}})
    return opened


class TestLinksPage:
    """链接页分组树."""

    def test_tree_groups_in_order(self, qtbot, ctl):
        """组按首现顺序, 未分组末尾; 子项为链接叶子."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        tree = page.tree
        assert tree.topLevelItemCount() == 3
        assert [tree.topLevelItem(i).text(0) for i in range(3)] == \
            ["信息化服务 (1)", "信息门户 (1)", "未分组 (1)"]
        first_group = tree.topLevelItem(0)
        assert first_group.childCount() == 1
        assert "学堂" in first_group.child(0).text(0)
        assert "learn" in first_group.child(0).text(1)  # URL 第二列
        assert tree.topLevelItem(2).child(0).data(0, ROLE_LINK).name == "散链"

    def test_double_click_opens_leaf(self, qtbot, ctl, fake_opener):
        """双击叶子经 URL 分发层打开; 双击组节点不打开."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        leaf = page.tree.topLevelItem(0).child(0)
        page._on_double_clicked(leaf, 0)
        assert fake_opener == ["https://learn.example.com"]
        page._on_double_clicked(page.tree.topLevelItem(0), 0)
        assert fake_opener == ["https://learn.example.com"]  # 组节点无效

    def test_current_group_context(self, qtbot, ctl):
        """组节点返回组名, 叶子返回其分组, 无选中返回空串."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        assert page._current_group() == ""
        group_node = page.tree.topLevelItem(1)
        page.tree.setCurrentItem(group_node)
        assert page._current_group() == "信息门户"
        page.tree.setCurrentItem(group_node.child(0))
        assert page._current_group() == "信息门户"  # 叶子回退到所属组
        page.tree.setCurrentItem(page.tree.topLevelItem(2).child(0))
        assert page._current_group() == ""  # 未分组叶子

    def test_reload_drops_empty_group(self, qtbot, ctl):
        """删除某组最后一条链接后, reload 该组消失."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        ctl.remove_quick_link("门户")  # 信息门户组唯一条目
        page.reload()
        assert page.tree.topLevelItemCount() == 2
        assert [page.tree.topLevelItem(i).text(0) for i in range(2)] == \
            ["信息化服务 (1)", "未分组 (1)"]

    def test_font_scaling_updates_icons(self, qtbot, ctl, monkeypatch):
        """字号联动刷新两级图标尺寸(与当前全局字号无关的相对断言)."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        leaf = page.tree.topLevelItem(0).child(0)
        small = max(s.width() for s in leaf.icon(0).availableSizes())
        bigger_px = theme.current_font_px() + 7
        monkeypatch.setattr(theme, "_state",
                            {**theme._state, "font_px": bigger_px})
        page.set_font_px(bigger_px)
        big = max(s.width() for s in leaf.icon(0).availableSizes())
        assert big > small


class TestLinkDialog:
    """链接编辑对话框."""

    def test_values_stripped(self, qtbot):
        """values 返回去空白后的 (名称, URL, 分组)."""
        dialog = LinkDialog("添加链接")
        qtbot.addWidget(dialog)
        dialog.name_edit.setText("  学堂  ")
        dialog.url_edit.setText("  https://learn.example.com  ")
        dialog.group_box.setCurrentText("  AI工具  ")
        assert dialog.values() == ("学堂", "https://learn.example.com",
                                   "AI工具")

    def test_default_group_and_choices(self, qtbot):
        """default_group 预填下拉文本, group_choices 提供候选."""
        dialog = LinkDialog("添加链接", default_group="AI工具",
                            group_choices=["信息门户", "信息化服务"])
        qtbot.addWidget(dialog)
        assert dialog.group_box.currentText() == "AI工具"
        assert [dialog.group_box.itemText(i)
                for i in range(dialog.group_box.count())] == \
            ["信息门户", "信息化服务"]

    def test_initial_prefill(self, qtbot):
        """编辑模式预填名称/URL/分组三元组."""
        dialog = LinkDialog("编辑链接",
                            initial=("学堂", "https://x.example.com", "AI工具"))
        qtbot.addWidget(dialog)
        assert dialog.values() == ("学堂", "https://x.example.com", "AI工具")

    def test_invalid_keeps_dialog_open(self, qtbot, monkeypatch):
        """空名称或非法 URL 警告且不关闭; 分组任意文本均可."""
        warnings = []
        monkeypatch.setattr("shuimu.gui.links_page.warn",
                            lambda _p, title, text: warnings.append(title))
        dialog = LinkDialog("添加链接")
        qtbot.addWidget(dialog)
        dialog._accept_if_valid()
        assert warnings == ["无法保存"]  # 名称为空
        dialog.name_edit.setText("学堂")
        dialog.url_edit.setText("x.example.com")
        dialog._accept_if_valid()
        assert warnings == ["无法保存", "无法保存"]  # URL 前缀非法
        assert dialog.result() != QDialog.DialogCode.Accepted  # 未 accept
