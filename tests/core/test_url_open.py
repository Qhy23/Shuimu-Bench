"""url_open.py 单元测试: 打开方式注册与分发."""

import pytest

from shuimu.core import url_open


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """每个测试用干净的注册表, 结束自动还原(模块级状态不泄漏)."""
    monkeypatch.setattr(url_open, "_state",
                        {"default": None, "registry": {}})


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
