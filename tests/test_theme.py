"""theme.py 单元测试: 设计 token 与样式表应用."""

from shuimu import theme


class TestTokens:
    """设计 token 完整性."""

    def test_colors_are_hex(self):
        """配色均为合法 hex 颜色."""
        for name in ("PRIMARY", "PRIMARY_LIGHT", "PRIMARY_DARK", "BG_BASE",
                     "BG_PANEL", "BG_HOVER", "FG_TEXT", "FG_DIM", "BORDER"):
            value = getattr(theme, name)
            assert value.startswith("#") and len(value) == 7, name

    def test_qss_contains_tokens(self):
        """样式表中引用了核心 token 且无明显占位残留."""
        assert theme.PRIMARY in theme.QSS
        assert theme.BG_BASE in theme.QSS
        assert "{" in theme.QSS and "}" in theme.QSS
        assert "{FG_TEXT}" not in theme.QSS  # f-string 未渲染残留


class TestApplyTheme:
    """应用到 QApplication."""

    def test_apply_sets_stylesheet(self, qapp):
        """apply_theme 后 QApplication 持有非空样式表."""
        theme.apply_theme(qapp)
        assert theme.PRIMARY in qapp.styleSheet()


class TestScaling:
    """字号缩放与 QSS 生成."""

    def test_build_qss_font_and_family(self):
        """指定字号进入 QSS, 字体族与颜色齐备."""
        qss = theme.build_qss(16)
        assert "font-size: 16px" in qss
        assert "Noto Sans CJK SC" in qss
        assert theme.PRIMARY in qss

    def test_build_qss_scales_text_paddings_only(self):
        """文字邻近 padding 随 k 缩放, chrome(圆角/滚动条)固定."""
        default_qss = theme.build_qss(13)
        big_qss = theme.build_qss(16)
        # 按钮 padding 5px 14px -> 6px 17px (k=16/13)
        assert "padding: 5px 14px" in default_qss
        assert "padding: 6px 17px" in big_qss
        for qss in (default_qss, big_qss):
            assert theme.RADIUS in qss          # 圆角不变
            assert "width: 10px" in qss          # 滚动条宽不变

    def test_clamp_font_px(self):
        """边界夹取与非法回退."""
        assert theme.clamp_font_px(8) == 9
        assert theme.clamp_font_px(21) == 20
        assert theme.clamp_font_px(13) == 13
        assert theme.clamp_font_px("abc") == 13
        assert theme.clamp_font_px(None) == 13

    def test_apply_theme_updates_current(self, qapp):
        """apply_theme 写入当前字号并更新 QApplication 样式表."""
        theme.apply_theme(qapp, 16)
        assert theme.current_font_px() == 16
        assert "font-size: 16px" in qapp.styleSheet()
        theme.apply_theme(qapp)  # 恢复默认, 避免影响其他测试
        assert theme.current_font_px() == 13
