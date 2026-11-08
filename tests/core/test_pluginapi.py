"""pluginapi.py 单元测试: 契约数据类的构造与汇总."""

import pytest

from shuimu.core.pluginapi import PluginJob, finding, make_report


class TestFinding:
    """finding 构造."""

    def test_valid(self):
        """合法构造保留全部字段."""
        item = finding("warn", "secret_scan", "疑似密钥", "a.py:3")
        assert item.severity == "warn"
        assert (item.check, item.title, item.detail) == (
            "secret_scan", "疑似密钥", "a.py:3")

    @pytest.mark.parametrize("bad", ["fatal", "", "OK", None, "error "])
    def test_invalid_severity_raises(self, bad):
        """非法严重度立即抛 ValueError."""
        with pytest.raises(ValueError):
            finding(bad, "check", "title")


class TestMakeReport:
    """make_report 汇总."""

    def test_error_makes_not_ok(self):
        """含 error 级 finding 时 ok 为 False."""
        report = make_report([finding("error", "c", "t")])
        assert report.ok is False

    def test_warn_only_is_ok(self):
        """仅 warn/ok 级时 ok 为 True."""
        report = make_report([finding("warn", "c", "t"), finding("ok", "c", "t")])
        assert report.ok is True

    def test_default_summary_counts(self):
        """缺省 summary 为计数式摘要."""
        report = make_report([
            finding("ok", "c", "t"), finding("warn", "c", "t"),
            finding("error", "c", "t"), finding("error", "c", "t")])
        assert report.summary == "2 项错误, 1 项警告, 1 项通过"

    def test_all_pass_summary(self):
        """零错误零警告时摘要标注全部通过."""
        report = make_report([finding("ok", "c", "t")])
        assert report.summary == "0 项错误, 0 项警告, 1 项通过, 全部通过"

    def test_empty_findings(self):
        """空检查列表 ok 且摘要为无检查项."""
        report = make_report([])
        assert report.ok is True
        assert report.summary == "无检查项"

    def test_explicit_summary_and_outputs(self):
        """显式 summary 与 outputs 原样保留."""
        report = make_report([], summary="自定义", outputs=["/tmp/a"])
        assert report.summary == "自定义"
        assert report.outputs == ["/tmp/a"]


class TestJob:
    """PluginJob 默认值."""

    def test_defaults_are_independent(self):
        """两个实例的默认容器互不影响."""
        job_a, job_b = PluginJob(), PluginJob()
        job_a.targets.append("x")
        job_a.params["k"] = 1
        assert not job_b.targets
        assert not job_b.params
