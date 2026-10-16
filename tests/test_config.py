"""config.py 单元测试: 配置加载, 合并与回退."""

from shuimu.config import DEFAULTS, load_config, organize_rules


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
