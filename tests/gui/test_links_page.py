"""links_page.py 测试: 链接页列表/打开与 LinkDialog 校验."""

import pytest

from PySide6.QtWidgets import QDialog

from shuimu.core import url_open
from shuimu.core.controller import Controller
from shuimu.core.storage import Storage
from shuimu.gui import theme
from shuimu.gui.links_page import LinkDialog, LinksPage


@pytest.fixture()
def ctl(tmp_path):
    """独立 controller: 预置两条链接的临时数据文件(不依赖仓库模板)."""
    links_path = tmp_path / "links.yaml"
    links_path.write_text(
        "links:\n"
        "  - name: 学堂\n"
        "    url: https://learn.example.com\n"
        "  - name: 门户\n"
        "    url: https://info.example.com\n", encoding="utf-8")
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
    """链接页."""

    def test_lists_links(self, qtbot, ctl):
        """构造后列表含数据文件里的全部链接."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        assert page.list_widget.count() == 2
        assert "学堂" in page.list_widget.item(0).text()

    def test_open_item_dispatches(self, qtbot, ctl, fake_opener):
        """打开条目经 URL 分发层默认处理器."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        page._open_item(page.list_widget.item(0))
        assert fake_opener == ["https://learn.example.com"]

    def test_reload_after_controller_change(self, qtbot, ctl):
        """controller 变更后 reload 反映新状态."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        ctl.remove_quick_link("门户")
        page.reload()
        assert page.list_widget.count() == 1

    def test_font_scaling_updates_icons(self, qtbot, ctl, monkeypatch):
        """字号联动刷新列表图标尺寸(与当前全局字号无关的相对断言)."""
        page = LinksPage(ctl)
        qtbot.addWidget(page)
        small = max(s.width() for s
                    in page.list_widget.item(0).icon().availableSizes())
        bigger_px = theme.current_font_px() + 7
        monkeypatch.setattr(theme, "_state", {"font_px": bigger_px})
        page.set_font_px(bigger_px)
        big = max(s.width() for s
                  in page.list_widget.item(0).icon().availableSizes())
        assert big > small


class TestLinkDialog:
    """链接编辑对话框."""

    def test_values_stripped(self, qtbot):
        """values 返回去空白后的 (名称, URL)."""
        dialog = LinkDialog("添加链接")
        qtbot.addWidget(dialog)
        dialog.name_edit.setText("  学堂  ")
        dialog.url_edit.setText("  https://learn.example.com  ")
        assert dialog.values() == ("学堂", "https://learn.example.com")

    def test_initial_prefill(self, qtbot):
        """编辑模式预填初始值."""
        dialog = LinkDialog("编辑链接", initial=("学堂", "https://x.example.com"))
        qtbot.addWidget(dialog)
        assert dialog.values() == ("学堂", "https://x.example.com")

    def test_invalid_keeps_dialog_open(self, qtbot, monkeypatch):
        """空名称或非法 URL 警告且不关闭."""
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
