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
