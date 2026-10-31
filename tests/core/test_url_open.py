"""url_open.py 单元测试: 打开方式注册, 分发与系统打开的标准库实现."""

import pytest

from shuimu.core import url_open


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """每个测试用干净的注册表, 结束自动还原(模块级状态不泄漏)."""
    monkeypatch.setattr(url_open, "_state",
                        {"default": url_open._no_opener, "registry": {}})


class TestDefault:
    """默认处理器."""

    def test_no_default_returns_false(self):
        """未设置默认处理器时打开失败."""
        assert url_open.open_url("https://example.com") is False

    def test_default_receives_url(self):
        """key 缺省走默认处理器并透传 URL."""
        opened = []

        def fake(url):
            opened.append(url)
            return True

        url_open.set_default(fake)
        assert url_open.open_url("https://example.com") is True
        assert opened == ["https://example.com"]


class TestRegistry:
    """具名处理器."""

    def test_register_and_open_by_key(self):
        """按 key 调用对应处理器."""
        opened = []
        url_open.register("embedded", "内嵌浏览器",
                          lambda url: opened.append(url) or True)
        assert url_open.openers() == [("embedded", "内嵌浏览器")]
        assert url_open.open_url("https://example.com", key="embedded") is True
        assert opened == ["https://example.com"]

    def test_unknown_key_returns_false(self):
        """未注册的 key 打开失败."""
        url_open.register("embedded", "内嵌浏览器", lambda url: True)
        assert url_open.open_url("https://example.com", key="nope") is False

    def test_reregister_overrides(self):
        """同 key 重复注册以后者为准."""
        calls = []
        url_open.register("a", "旧", lambda url: calls.append("old") or True)
        url_open.register("a", "新", lambda url: calls.append("new") or True)
        assert url_open.openers() == [("a", "新")]
        assert url_open.open_url("x", key="a") is True
        assert calls == ["new"]

    def test_register_none_removes(self):
        """fn 传 None 移除该注册项."""
        url_open.register("a", "甲", lambda url: True)
        url_open.register("a", "甲", None)
        assert url_open.openers() == []
        assert url_open.open_url("x", key="a") is False

    def test_specified_key_does_not_touch_default(self):
        """按 key 打开不经默认处理器."""
        default_calls = []
        url_open.set_default(lambda url: default_calls.append(url) or True)
        embedded_calls = []
        url_open.register("embedded", "内嵌浏览器",
                          lambda url: embedded_calls.append(url) or True)
        assert url_open.open_url("https://example.com", key="embedded") is True
        assert embedded_calls == ["https://example.com"]
        assert not default_calls


class _FakeResult:
    """subprocess.run 结果替身."""

    def __init__(self, returncode=0):
        self.returncode = returncode


class TestWslviewOpen:
    """WSL 的 wslview 分发."""

    def test_not_in_wsl_returns_none(self, monkeypatch):
        """非 WSL 返回 None(由调用方回退)."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: False)
        assert url_open.wslview_open("/x/a.pdf") is None

    def test_in_wsl_without_wslview_returns_none(self, monkeypatch):
        """WSL 内未装 wslview 返回 None."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: True)
        monkeypatch.setattr(url_open.shutil, "which", lambda name: None)
        assert url_open.wslview_open("/x/a.pdf") is None

    def test_in_wsl_dispatches(self, monkeypatch):
        """WSL 内有 wslview 时经 subprocess 调用并回传成败."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: True)
        monkeypatch.setattr(url_open.shutil, "which",
                            lambda name: "/usr/bin/wslview")
        ran = []
        monkeypatch.setattr(
            url_open.subprocess, "run",
            lambda cmd, check, timeout:
            ran.append((cmd, check, timeout)) or _FakeResult(0))
        assert url_open.wslview_open("/x/a.pdf") is True
        assert ran == [(["/usr/bin/wslview", "/x/a.pdf"], False, 15)]


class TestOpenUrlExternal:
    """纯标准库的系统浏览器打开(CLI 默认处理器)."""

    def test_wsl_prefers_wslview(self, monkeypatch):
        """WSL 内走 wslview 分发."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: True)
        seen = []
        monkeypatch.setattr(url_open, "wslview_open",
                            lambda target: seen.append(target) or True)
        assert url_open.open_url_external("https://example.com") is True
        assert seen == ["https://example.com"]

    def test_linux_uses_xdg_open(self, monkeypatch):
        """Linux 经 _run_opener 调 xdg-open."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: False)
        monkeypatch.setattr(url_open.sys, "platform", "linux")
        monkeypatch.setattr(url_open, "_run_opener",
                            lambda tool, u: tool == "xdg-open")
        assert url_open.open_url_external("https://example.com") is True

    def test_macos_uses_open(self, monkeypatch):
        """macOS 选 open 工具."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: False)
        monkeypatch.setattr(url_open.sys, "platform", "darwin")
        monkeypatch.setattr(url_open, "_run_opener",
                            lambda tool, u: tool == "open")
        assert url_open.open_url_external("https://example.com") is True

    def test_windows_startfile(self, monkeypatch):
        """Windows 用 os.startfile."""
        monkeypatch.setattr(url_open, "in_wsl", lambda: False)
        monkeypatch.setattr(url_open.sys, "platform", "win32")
        opened = []
        monkeypatch.setattr(url_open.os, "startfile", opened.append,
                            raising=False)
        assert url_open.open_url_external("https://example.com") is True
        assert opened == ["https://example.com"]

    def test_run_opener_tool_missing(self, monkeypatch):
        """PATH 无该工具返回 False."""
        monkeypatch.setattr(url_open.shutil, "which", lambda name: None)
        assert url_open._run_opener("xdg-open", "https://example.com") is False

    def test_run_opener_nonzero_returncode(self, monkeypatch):
        """打开器返回非 0 视为失败."""
        monkeypatch.setattr(url_open.shutil, "which",
                            lambda name: "/usr/bin/xdg-open")
        monkeypatch.setattr(url_open.subprocess, "run",
                            lambda cmd, check, timeout: _FakeResult(1))
        assert url_open._run_opener("xdg-open", "https://example.com") is False
