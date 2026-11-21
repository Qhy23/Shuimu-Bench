"""文件类型图标测试(系统图标缺失时走内置扩展名回退)."""

from PySide6.QtGui import QIcon

from shuimu.gui import file_icons


def test_extension_normalization():
    """扩展名统一小写, 无扩展名返回空串."""
    assert file_icons.extension("课程讲义.PDF") == "pdf"
    assert file_icons.extension("README") == ""


def test_fallback_icons_nonempty(qapp):
    """常见文件类型均有非空图标."""
    assert qapp is not None
    for filename in ("a.pdf", "a.docx", "a.xlsx", "a.py", "a.zip", "a.png"):
        icon = file_icons.file_type_icon(filename)
        assert not icon.isNull(), filename
        assert icon.availableSizes()


def test_system_icon_preferred(qapp, monkeypatch):
    """系统图标可用时优先使用, 保持资源管理器观感."""
    assert qapp is not None
    sentinel = QIcon(file_icons._draw_type_icon("SYS", "#123456", 16))

    class Provider:
        """返回固定系统图标的替身."""

        def icon(self, _info):
            """返回预设图标."""
            return sentinel

    monkeypatch.setattr(file_icons, "QFileIconProvider", Provider)
    file_icons.clear_cache()
    assert file_icons.file_type_icon("a.pdf") is sentinel
    file_icons.clear_cache()


def test_failed_file_uses_gray_fallback(qapp, monkeypatch):
    """失效文件不查询系统, 仍提供灰色回退图标."""
    assert qapp is not None

    class Provider:
        """不应被实例化使用的系统图标替身."""

        def icon(self, _info):
            """失效文件不应调用此接口."""
            raise AssertionError("失效文件不应查询系统图标")

    monkeypatch.setattr(file_icons, "QFileIconProvider", Provider)
    file_icons.clear_cache()
    assert not file_icons.file_type_icon("gone.pdf", failed=True).isNull()
    file_icons.clear_cache()
