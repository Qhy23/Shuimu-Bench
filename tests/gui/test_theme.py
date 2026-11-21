"""theme.py 单元测试: 主题 token, 内置双主题, 用户主题扩展与样式表应用."""

import pytest

from shuimu.gui import artwork, theme


class TestTokens:
    """设计 token 完整性."""

    def test_builtin_themes_colors_are_hex(self):
        """内置双主题全部颜色 token 均为 #rrggbb."""
        for tk in theme.BUILTIN_THEMES.values():
            for field in theme.COLOR_FIELDS:
                value = getattr(tk, field)
                assert value.startswith("#") and len(value) == 7, \
                    f"{tk.name}.{field}={value}"
                int(value[1:], 16)

    def test_rich_text_and_lists_follow_tokens(self):
        """AI/学堂富文本与列表控件纳入主题选择器."""
        qss = theme.build_qss(13)
        for selector in ("QTextBrowser", "QListWidget", "QTextEdit"):
            assert selector in qss

    def test_dark_light_distinct(self):
        """亮暗两套主题底/面板/文字色可区分."""
        dark, light = theme.DARK_TOKENS, theme.LIGHT_TOKENS
        assert dark.base != light.base
        assert dark.panel != light.panel
        assert dark.fg != light.fg

    def test_qss_contains_tokens(self):
        """样式表中引用了核心 token 且无明显占位残留."""
        tk = theme.tokens()
        assert tk.accent in theme.QSS
        assert tk.base in theme.QSS
        assert "{" in theme.QSS and "}" in theme.QSS
        assert "{fg}" not in theme.QSS  # f-string 未渲染残留


class TestUserThemes:
    """用户主题文件加载与容错."""

    def _write(self, tmp_path, filename, content):
        path = tmp_path / filename
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_valid_override(self, tmp_path):
        """合法用户主题: 继承 dark 覆盖 accent, id 用文件名."""
        self._write(tmp_path, "ocean.yaml",
                    'name: 海蓝\nextends: dark\ncolors:\n'
                    '  accent: "#0b5cad"\n  selection: "#123456"\n')
        themes, warnings = theme.load_user_themes(str(tmp_path))
        assert not warnings
        assert themes["ocean"].accent == "#0b5cad"
        assert themes["ocean"].selection == "#123456"
        # 未覆盖的 token 继承基主题
        assert themes["ocean"].base == theme.DARK_TOKENS.base
        assert themes["ocean"].label == "海蓝"

    def test_short_hex_normalized(self, tmp_path):
        """#rgb 短写法归一化为 #rrggbb."""
        self._write(tmp_path, "t.yaml", "colors:\n  accent: \"#abc\"\n")
        themes, _w = theme.load_user_themes(str(tmp_path))
        assert themes["t"].accent == "#aabbcc"

    def test_unknown_key_warns_and_ignored(self, tmp_path):
        """未知 token 键告警并忽略, 主题仍可用."""
        self._write(tmp_path, "t.yaml", "colors:\n  nosuch: \"#ffffff\"\n")
        themes, warnings = theme.load_user_themes(str(tmp_path))
        assert "t" in themes
        assert any("nosuch" in w for w in warnings)

    def test_bad_extends_skipped(self, tmp_path):
        """extends 非内置主题时整个文件跳过."""
        self._write(tmp_path, "t.yaml",
                    'extends: solarized\ncolors:\n  accent: "#0b5cad"\n')
        themes, warnings = theme.load_user_themes(str(tmp_path))
        assert "t" not in themes
        assert any("extends" in w for w in warnings)

    def test_invalid_color_warns_and_ignored(self, tmp_path):
        """非法颜色值告警并忽略该键."""
        self._write(tmp_path, "t.yaml",
                    'colors:\n  accent: "blue"\n  fg: "#112233"\n')
        themes, warnings = theme.load_user_themes(str(tmp_path))
        assert themes["t"].accent == theme.DARK_TOKENS.accent
        assert themes["t"].fg == "#112233"
        assert any("accent" in w for w in warnings)

    def test_corrupt_and_non_mapping_skipped(self, tmp_path):
        """损坏 YAML 与非映射内容跳过并告警, 不影响同目录其他主题."""
        self._write(tmp_path, "bad.yaml", "colors: [unclosed\n")
        self._write(tmp_path, "list.yaml", "- just\n- a list\n")
        self._write(tmp_path, "good.yaml", "colors:\n  accent: \"#0b5cad\"\n")
        themes, warnings = theme.load_user_themes(str(tmp_path))
        assert list(themes) == ["good"]
        assert len(warnings) == 2

    def test_missing_dir_empty(self, tmp_path):
        """主题目录缺失返回空清单."""
        themes, warnings = theme.load_user_themes(
            str(tmp_path / "nope"))
        assert not themes and not warnings

    def test_theme_choices_builtin_first(self, tmp_path):
        """清单内置在前, 用户主题随后."""
        self._write(tmp_path, "mine.yaml", "colors:\n  accent: \"#0b5cad\"\n")
        choices = theme.theme_choices(str(tmp_path))
        names = [n for n, _label in choices]
        assert names[:2] == ["dark", "light"]
        assert "mine" in names


class TestApplyTheme:
    """应用到 QApplication."""

    def test_apply_sets_stylesheet(self, qapp):
        """apply_theme 后 QApplication 持有非空样式表."""
        theme.theme_choices()  # 同步用户主题快照
        theme.apply_theme(qapp, theme="dark")
        assert theme.tokens().accent in qapp.styleSheet()
        theme.apply_theme(qapp, theme="dark")

    def test_switch_theme_updates_tokens_and_clears_artwork(self, qapp):
        """切换主题更新 token 并清空 artwork 缓存."""
        theme.theme_choices()  # 同步用户主题快照
        theme.apply_theme(qapp, theme="dark")
        artwork.bench_icon(32)  # 填充母版缓存
        assert artwork._cache["master"] is not None  # noqa: SLF001
        theme.apply_theme(qapp, theme="light")
        assert theme.current_name() == "light"
        assert theme.tokens().base == theme.LIGHT_TOKENS.base
        assert artwork._cache["master"] is None  # noqa: SLF001
        artwork.bench_icon(32)
        theme.apply_theme(qapp, theme="dark")
        assert theme.current_name() == "dark"
        assert artwork._cache["master"] is None  # noqa: SLF001

    def test_unknown_theme_raises(self, qapp):
        """未知主题 id 抛 ValueError, 当前主题不变."""
        theme.theme_choices()  # 同步用户主题快照
        theme.apply_theme(qapp, theme="dark")
        with pytest.raises(ValueError):
            theme.apply_theme(qapp, theme="nosuch")
        assert theme.current_name() == "dark"


class TestScaling:
    """字号缩放与 QSS 生成."""

    def test_build_qss_font_and_family(self):
        """指定字号进入 QSS, 字体族与颜色齐备."""
        qss = theme.build_qss(16)
        assert "font-size: 16px" in qss
        assert "Noto Sans CJK SC" in qss
        assert theme.tokens().accent in qss

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

    def test_apply_theme_updates_current(self, qapp):
        """apply_theme 写入当前字号并更新 QApplication 样式表."""
        theme.theme_choices()  # 同步用户主题快照
        theme.apply_theme(qapp, font_px=16, theme="dark")
        assert theme.current_font_px() == 16
        assert "font-size: 16px" in qapp.styleSheet()
        theme.apply_theme(qapp, font_px=13, theme="dark")
        assert theme.current_font_px() == 13


class TestButtonTiers:
    """按钮层级进入 QSS."""

    def test_primary_and_danger_in_qss(self):
        """primary/danger/tool 三级按钮样式在 QSS 中."""
        qss = theme.build_qss(13)
        assert 'QPushButton[primary="true"]' in qss
        assert 'QPushButton[danger="true"]' in qss
        assert 'QPushButton[tool="true"]' in qss


class TestCoursePalette:
    """课程色板与主题无关."""

    def test_course_color_stable(self):
        """同名课全学期同色且落在色板内."""
        assert len(theme.COURSE_PALETTE) == 11
        assert theme.course_color("AI 编程进阶") == \
            theme.course_color("AI 编程进阶")
        assert theme.course_color("AI 编程进阶") in theme.COURSE_PALETTE


class TestTypographyHierarchy:
    """文字三级层次(对齐 thu-info-app 阶梯)."""

    def test_hierarchy_sizes_in_qss(self):
        """title/body/caption 三级字号与等宽字体进入 QSS."""
        qss = theme.build_qss(16)
        assert "font-size: 22px" in qss  # title = round(16*1.35)
        assert "font-size: 16px" in qss  # body
        assert "font-size: 14px" in qss  # caption = round(16*0.85)
        assert "JetBrainsMono NFM" in qss
        assert "QLabel#apptitle" in qss
        assert "QLabel#cardtime" in qss

    def test_hierarchy_scales(self):
        """分级随缩放联动."""
        assert "font-size: 18px" in theme.build_qss(13)   # title 13*1.35
        assert "font-size: 11px" in theme.build_qss(13)   # caption 0.85
