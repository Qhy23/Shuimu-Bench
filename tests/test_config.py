"""config.py 单元测试: 配置加载, 合并与回退."""

from datetime import date

from shuimu.config import (
    DEFAULTS,
    DEFAULT_FONT_PX,
    clamp_font_px,
    font_size_value,
    load_config,
    organize_rules,
    semester_start_date,
    semester_week_count,
)


class TestLoadConfig:
    """加载与回退."""

    def test_defaults_when_no_file(self, tmp_path):
        """指定路径不存在时返回内置默认值."""
        cfg = load_config(str(tmp_path / "nope.yaml"))
        assert cfg == DEFAULTS

    def test_user_file_overrides_partial(self, tmp_path):
        """用户文件只覆盖显式给出的键, 其余保留默认."""
        f = tmp_path / "config.yaml"
        f.write_text('app:\n  title: "自定义标题"\n', encoding="utf-8")
        cfg = load_config(str(f))
        assert cfg["app"]["title"] == "自定义标题"
        assert cfg["app"]["window_size"] == DEFAULTS["app"]["window_size"]
        assert cfg["course"]["default_name"] == "未分组"

    def test_nested_merge_organize(self, tmp_path):
        """organize.categories 深合并, 可加新类别."""
        f = tmp_path / "config.yaml"
        f.write_text(
            "organize:\n  categories:\n    截图: [\".png\"]\n", encoding="utf-8")
        cfg = load_config(str(f))
        assert ".png" in cfg["organize"]["categories"]["截图"]
        assert "文档" in cfg["organize"]["categories"]  # 原有类别保留

    def test_broken_file_falls_back(self, tmp_path):
        """损坏的 yaml 不炸, 回退默认值."""
        f = tmp_path / "config.yaml"
        f.write_text("{{{not yaml", encoding="utf-8")
        assert load_config(str(f)) == DEFAULTS

    def test_non_dict_root_ignored(self, tmp_path):
        """根节点不是字典时忽略该文件."""
        f = tmp_path / "config.yaml"
        f.write_text("- just\n- a\n- list\n", encoding="utf-8")
        assert load_config(str(f)) == DEFAULTS


class TestOrganizeRules:
    """整理规则提取."""

    def test_shape(self):
        """返回 categories 与 fallback 两个键."""
        rules = organize_rules(DEFAULTS)
        assert "文档" in rules["categories"]
        assert rules["fallback"] == "其他"


class TestSemesterStartDate:
    """学期起点解析."""

    def test_empty_and_missing(self):
        """空串与缺失返回 None."""
        assert semester_start_date(DEFAULTS) is None
        assert semester_start_date({}) is None
        assert semester_start_date({"schedule": None}) is None

    def test_valid_string(self):
        """合法 YYYY-MM-DD 字符串."""
        cfg = {"schedule": {"semester_start": "2026-09-14"}}
        assert semester_start_date(cfg) == date(2026, 9, 14)

    def test_invalid_format_falls_back(self):
        """非法格式静默回退 None."""
        cfg = {"schedule": {"semester_start": "2026/09/14"}}
        assert semester_start_date(cfg) is None

    def test_yaml_date_object(self):
        """YAML 未加引号日期解析成的 date 对象一并接受."""
        cfg = {"schedule": {"semester_start": date(2026, 9, 14)}}
        assert semester_start_date(cfg) == date(2026, 9, 14)


class TestSemesterWeekCount:
    """学期总周数解析."""

    def test_default_zero(self):
        """未配置返回 0(不限制)."""
        assert semester_week_count(DEFAULTS) == 0
        assert semester_week_count({}) == 0

    def test_valid(self):
        """整数与数字字符串都接受."""
        assert semester_week_count({"schedule": {"week_count": 18}}) == 18
        assert semester_week_count({"schedule": {"week_count": "18"}}) == 18

    def test_invalid_falls_back_zero(self):
        """非法/非正数返回 0."""
        assert semester_week_count({"schedule": {"week_count": "abc"}}) == 0
        assert semester_week_count({"schedule": {"week_count": -3}}) == 0
        assert semester_week_count({"schedule": None}) == 0


class TestClampFontPx:
    """字号夹取."""

    def test_clamp_font_px(self):
        """边界夹取与非法回退."""
        assert clamp_font_px(8) == 9
        assert clamp_font_px(21) == 20
        assert clamp_font_px(13) == 13
        assert clamp_font_px("abc") == 13
        assert clamp_font_px(None) == 13

    def test_default_font_matches_defaults(self):
        """DEFAULT_FONT_PX 与 DEFAULTS 内置字号一致."""
        assert DEFAULT_FONT_PX == DEFAULTS["app"]["font_size"]


class TestFontSizeValue:
    """界面字号提取."""

    def test_default(self):
        """缺省 13."""
        assert font_size_value(DEFAULTS) == 13
        assert font_size_value({}) == 13

    def test_clamped(self):
        """越界夹取到 [9, 20]."""
        assert font_size_value({"app": {"font_size": 4}}) == 9
        assert font_size_value({"app": {"font_size": 99}}) == 20

    def test_invalid_falls_back(self):
        """非法回退默认."""
        assert font_size_value({"app": {"font_size": "big"}}) == 13
        assert font_size_value({"app": None}) == 13
