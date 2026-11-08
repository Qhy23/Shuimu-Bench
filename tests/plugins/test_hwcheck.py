"""hwcheck 插件测试: 规则包解析与五类检查(直接调 run, 不依赖框架接入)."""

import pytest
import yaml

from shuimu.core.pluginapi import PluginJob
from shuimu.plugins.hwcheck import plugin

STUDENT_ID = "20231234567"   # 202 + 8 位, 匹配 strict 的学号正则


def job(targets, **params):
    """构造 hwcheck 的输入."""
    return PluginJob(targets=list(targets), params=params)


def by_check(report, check):
    """按检查项过滤 findings."""
    return [f for f in report.findings if f.check == check]


def write_pack(path, raw):
    """写一个自定义规则包 YAML."""
    path.write_text(yaml.safe_dump(raw, allow_unicode=True),
                    encoding="utf-8")
    return str(path)


@pytest.fixture
def hwdir(tmp_path):
    """合规的严格规范作业目录: 学号命名 PDF/ZIP + 源代码, 无密钥."""
    (tmp_path / f"{STUDENT_ID}-report.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / f"{STUDENT_ID}-code.zip").write_bytes(b"PK\x03\x04")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n",
                                              encoding="utf-8")
    return tmp_path


class TestRulePacks:
    """规则包加载与解析."""

    def test_builtin_packs_load(self):
        """内置 basic/strict 按名加载."""
        assert plugin.load_rules("basic")["name"] == "基础规范"
        assert plugin.load_rules("strict")["name"] == "严格规范"

    def test_unknown_pack_raises(self):
        """未知名且非文件路径抛 ValueError."""
        with pytest.raises(ValueError, match="规则包不存在"):
            plugin.load_rules("ghost")

    def test_custom_pack_path(self, tmp_path):
        """自定义规则包按路径加载."""
        path = write_pack(tmp_path / "my.yaml", {"name": "自定义"})
        assert plugin.load_rules(path)["name"] == "自定义"

    def test_invalid_regex_raises(self, tmp_path):
        """规则包正则非法抛 ValueError."""
        path = write_pack(tmp_path / "bad.yaml", {
            "name": "坏", "name_checks": [
                {"applies": "*.pdf", "pattern": "("}]})
        with pytest.raises(ValueError, match="正则非法"):
            plugin.load_rules(path)

    def test_missing_name_raises(self, tmp_path):
        """规则包缺 name 抛 ValueError."""
        path = write_pack(tmp_path / "noname.yaml", {"required_files": []})
        with pytest.raises(ValueError, match="缺 name"):
            plugin.load_rules(path)

    def test_unknown_key_becomes_warn_finding(self, tmp_path, hwdir):
        """规则包未知顶层键转为 check=rules 的警告."""
        path = write_pack(tmp_path / "extra.yaml",
                          {"name": "带未知键", "future": 1})
        report = plugin.run(job([str(hwdir)], rules_path=path))
        warns = by_check(report, "rules")
        assert any("规则包未知键: future" in f.title for f in warns)


class TestRequiredFiles:
    """必交文件检查."""

    def test_missing_pdf_error(self, tmp_path):
        """空目录缺报告 PDF -> error 且 report.ok 为 False."""
        report = plugin.run(job([str(tmp_path)]))
        errors = by_check(report, "required_files")
        assert errors[0].severity == "error"
        assert "报告 PDF" in errors[0].title
        assert report.ok is False

    def test_present_pdf_ok(self, hwdir):
        """齐全目录必交项全通过."""
        report = plugin.run(job([str(hwdir)], rules="strict"))
        oks = [f for f in by_check(report, "required_files")
               if f.severity == "ok"]
        assert len(oks) == 3
        assert report.ok is True

    def test_missing_zip_error(self, tmp_path):
        """strict 下缺压缩包报错."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        report = plugin.run(job([str(tmp_path)], rules="strict"))
        titles = [f.title for f in by_check(report, "required_files")]
        assert any("代码压缩包" in t for t in titles)


class TestForbiddenFiles:
    """禁止文件检查."""

    def test_pycache_hit(self, tmp_path):
        """__pycache__ 命中 forbidden."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "main.cpython-312.pyc").write_bytes(b"\x00")
        report = plugin.run(job([str(tmp_path)], rules="strict"))
        hits = by_check(report, "forbidden_files")
        assert hits[0].severity == "error"
        assert "Python 缓存" in hits[0].title
        assert "main.cpython-312.pyc" in hits[0].detail


class TestNameChecks:
    """文件名合规检查."""

    def test_name_without_id_error(self, tmp_path):
        """无学号文件名 -> error."""
        (tmp_path / f"{STUDENT_ID}.pdf").unlink(missing_ok=True)
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        report = plugin.run(job([str(tmp_path)], rules="strict"))
        errors = by_check(report, "name_checks")
        assert errors[0].severity == "error"
        assert "学号" in errors[0].title

    def test_compliant_names_ok(self, hwdir):
        """学号命名全部合规 -> ok finding."""
        report = plugin.run(job([str(hwdir)], rules="strict"))
        oks = [f for f in by_check(report, "name_checks")
               if f.severity == "ok"]
        assert len(oks) == 2


class TestSecretScan:
    """密钥泄漏扫描."""

    def test_pem_key_header(self, tmp_path):
        """PEM 私钥头命中 error, detail 含 文件:行号."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        (tmp_path / "a.py").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
        report = plugin.run(job([str(tmp_path)]))
        hits = by_check(report, "secret_scan")
        assert hits[0].severity == "error"
        assert "PEM" in hits[0].title
        assert "a.py:1" in hits[0].detail

    @pytest.mark.parametrize("secret,label", [
        ("AKIA1234567890ABCDEF", "AWS"),
        ("ghp_" + "A1b2C3d4E5f6G7h8J9k0L1m2N3o4P5q6R7s8", "GitHub Token"),
        ("sk-proj-" + "a1B2c3D4e5F6g7H8i9J0k1", "OpenAI"),
    ])
    def test_builtin_patterns_hit(self, tmp_path, secret, label):
        """内置密钥清单逐项命中 error."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        (tmp_path / "a.py").write_text(secret + "\n", encoding="utf-8")
        report = plugin.run(job([str(tmp_path)]))
        hits = by_check(report, "secret_scan")
        assert hits and hits[0].severity == "error"
        assert label in hits[0].title

    def test_jwt_is_warn(self, tmp_path):
        """JWT 命中为 warn 级."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        (tmp_path / "a.py").write_text(
            "token = eyJabcdefgh.ijklmnop.uvw\n", encoding="utf-8")
        report = plugin.run(job([str(tmp_path)]))
        hits = by_check(report, "secret_scan")
        assert hits[0].severity == "warn"
        assert "JWT" in hits[0].title

    def test_custom_pattern_appended(self, tmp_path):
        """自定义规则包追加内部平台密钥模式."""
        pack = write_pack(tmp_path / "my.yaml", {
            "name": "自定义", "secret_scan": {
                "patterns": [{"pattern": "sk-inner-[A-Za-z0-9]{10,}",
                              "label": "内部平台密钥"}]}})
        (tmp_path / "c.txt").write_text("sk-inner-abcdef12345\n",
                                        encoding="utf-8")
        report = plugin.run(job([str(tmp_path)], rules_path=pack))
        hits = by_check(report, "secret_scan")
        assert any("内部平台密钥" in f.title for f in hits)

    def test_binary_extension_not_scanned(self, tmp_path):
        """非文本扩展名(.png)不做内容扫描."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        (tmp_path / "pic.png").write_text("AKIA1234567890ABCDEF\n",
                                          encoding="utf-8")
        report = plugin.run(job([str(tmp_path)]))
        assert not by_check(report, "secret_scan")

    def test_git_dir_pruned(self, tmp_path):
        """.git 目录整目录剪除."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF")
        git = tmp_path / ".git"
        git.mkdir()
        (git / "config").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
        report = plugin.run(job([str(tmp_path)]))
        assert not by_check(report, "secret_scan")

    def test_extensions_override(self, tmp_path):
        """规则包 extensions 覆盖扫描扩展名集合."""
        pack = write_pack(tmp_path / "md-only.yaml", {
            "name": "仅md", "secret_scan": {"extensions": [".md"]}})
        (tmp_path / "a.py").write_text("AKIA1234567890ABCDEF\n",
                                       encoding="utf-8")
        report = plugin.run(job([str(tmp_path)], rules_path=pack))
        assert not by_check(report, "secret_scan")
        (tmp_path / "b.md").write_text("AKIA1234567890ABCDEF\n",
                                       encoding="utf-8")
        report = plugin.run(job([str(tmp_path)], rules_path=pack))
        assert by_check(report, "secret_scan")


class TestLargeFiles:
    """大文件警告."""

    def test_oversized_warns(self, tmp_path):
        """超过阈值的大文件给 warn."""
        (tmp_path / "big.bin").write_bytes(b"\0" * (2 * 1024 * 1024))
        report = plugin.run(job([str(tmp_path)], large_file_mb=1))
        hits = by_check(report, "large_file")
        assert hits[0].severity == "warn"
        assert "big.bin" in hits[0].title

    def test_zero_disables(self, tmp_path):
        """阈值 0 关闭检查."""
        (tmp_path / "big.bin").write_bytes(b"\0" * (2 * 1024 * 1024))
        report = plugin.run(job([str(tmp_path)], large_file_mb=0))
        assert not by_check(report, "large_file")


class TestExclude:
    """排除模式."""

    def test_excluded_file_not_reported(self, tmp_path):
        """exclude 命中的文件跳过内容扫描."""
        (tmp_path / "draft-a.py").write_text("AKIA1234567890ABCDEF\n",
                                             encoding="utf-8")
        (tmp_path / "keep.py").write_text("print('ok')\n", encoding="utf-8")
        report = plugin.run(job([str(tmp_path)], exclude="draft*",
                                only_problems=True))
        assert not by_check(report, "secret_scan")


class TestRunInput:
    """入口目标校验."""

    def test_empty_targets_error(self):
        """空目标 -> error finding."""
        report = plugin.run(job([]))
        assert report.ok is False
        assert by_check(report, "input")[0].severity == "error"

    def test_not_a_directory_error(self, tmp_path):
        """目标非目录 -> error finding."""
        target = tmp_path / "file.txt"
        target.write_text("x", encoding="utf-8")
        report = plugin.run(job([str(target)]))
        assert by_check(report, "input")[0].severity == "error"

    def test_multi_targets_warn_and_check_first(self, tmp_path, hwdir):
        """多目标仅检查第一个并给 warn."""
        other = tmp_path / "other"
        other.mkdir()
        report = plugin.run(job([str(hwdir), str(other)]))
        warns = by_check(report, "input")
        assert warns[0].severity == "warn"
        assert "仅检查第一个" in warns[0].title


class TestSummary:
    """汇总报告."""

    def test_summary_mentions_pack_and_counts(self, hwdir):
        """摘要含规则包名与文件数."""
        report = plugin.run(job([str(hwdir)], rules="strict"))
        assert report.summary.startswith("规则包 严格规范: 检查 3 个文件")
        assert report.ok is True

    def test_only_problems_filters_ok(self, hwdir):
        """only_problems 滤掉通过项, ok 判定不变."""
        report = plugin.run(job([str(hwdir)], rules="strict",
                                only_problems=True))
        assert report.ok is True
        assert not [f for f in report.findings if f.severity == "ok"]
