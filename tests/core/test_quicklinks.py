"""quicklinks.py 单元测试: 常用链接 YAML 读写与播种."""

import os

import pytest

from shuimu.core.paths import template_links_path
from shuimu.core.quicklinks import (
    QuickLink,
    ensure_links_file,
    load_quick_links,
    parse_quick_link,
    save_quick_links,
)


class TestParseQuickLink:
    """单条解析与校验."""

    def test_valid(self):
        """合法条目解析为 QuickLink, 前后空白被去除."""
        link = parse_quick_link({"name": " 网络学堂 ", "url": " https://x.com "})
        assert link == QuickLink("网络学堂", "https://x.com")

    @pytest.mark.parametrize("raw", [
        "not-a-map",
        {"name": "", "url": "https://x.com"},
        {"name": "x", "url": "ftp://x.com"},
        {"name": "x", "url": "x.com"},
        {"name": "x"},
    ], ids=["非映射", "空名称", "非http前缀", "无scheme", "缺url"])
    def test_invalid_raises(self, raw):
        """非法条目抛 ValueError."""
        with pytest.raises(ValueError):
            parse_quick_link(raw)


class TestLoadSave:
    """读写往返与容错."""

    def test_round_trip(self, tmp_path):
        """保存后读回一致, 文件头部说明在, 无 .tmp 残留."""
        path = str(tmp_path / "links.yaml")
        links = [QuickLink("学堂", "https://learn.example.com"),
                 QuickLink("门户", "https://info.example.com")]
        save_quick_links(path, links)
        assert load_quick_links(path) == links
        with open(path, encoding="utf-8") as f:
            assert "常用链接" in f.readline()
        assert not (tmp_path / "links.yaml.tmp").exists()

    def test_load_missing_file_returns_empty(self, tmp_path):
        """文件缺失返回空列表."""
        assert not load_quick_links(str(tmp_path / "none.yaml"))

    def test_load_broken_yaml_returns_empty(self, tmp_path):
        """YAML 损坏整体回退为空."""
        path = tmp_path / "broken.yaml"
        path.write_text("links: [unclosed", encoding="utf-8")
        assert not load_quick_links(str(path))

    def test_load_toplevel_not_map_returns_empty(self, tmp_path):
        """顶层非映射回退为空."""
        path = tmp_path / "list.yaml"
        path.write_text("- a\n- b\n", encoding="utf-8")
        assert not load_quick_links(str(path))

    def test_load_skips_invalid_entries(self, tmp_path):
        """单条非法跳过, 其余正常返回."""
        path = tmp_path / "mixed.yaml"
        path.write_text(
            "links:\n"
            "  - name: 学堂\n"
            "    url: https://learn.example.com\n"
            "  - name: 坏条目\n"
            "    url: not-a-url\n"
            "  - name: 门户\n"
            "    url: https://info.example.com\n", encoding="utf-8")
        assert load_quick_links(str(path)) == [
            QuickLink("学堂", "https://learn.example.com"),
            QuickLink("门户", "https://info.example.com"),
        ]


class TestEnsureLinksFile:
    """缺失播种."""

    def test_repo_template_valid(self):
        """仓库分发的模板可解析且条目全部合法."""
        template = template_links_path()
        assert os.path.isfile(template)
        links = load_quick_links(template)
        assert links  # 模板非空
        assert all(isinstance(link, QuickLink) for link in links)
        assert len({link.name for link in links}) == len(links)  # 名称唯一

    def test_seeds_from_template(self, tmp_path):
        """有模板时复制模板内容."""
        template = tmp_path / "tpl.yaml"
        template.write_text(
            "links:\n  - name: 模板站\n    url: https://tpl.example.com\n",
            encoding="utf-8")
        path = str(tmp_path / "sub" / "links.yaml")
        assert ensure_links_file(path, str(template)) is True
        assert load_quick_links(path) == [
            QuickLink("模板站", "https://tpl.example.com")]

    def test_seeds_skeleton_without_template(self, tmp_path):
        """模板缺失时写空骨架, 仍可正常读取."""
        path = str(tmp_path / "links.yaml")
        assert ensure_links_file(path) is True
        assert not load_quick_links(path)

    def test_idempotent(self, tmp_path):
        """文件已存在时不写入."""
        path = tmp_path / "links.yaml"
        path.write_text("links: []\n", encoding="utf-8")
        assert ensure_links_file(str(path)) is False
        assert path.read_text(encoding="utf-8") == "links: []\n"
