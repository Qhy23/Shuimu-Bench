"""favicon 的网页声明解析与磁盘缓存测试(全部传输替身, 零网络)."""

import pytest

from shuimu.core import favicon


class TestCandidates:
    """网页 favicon 声明与默认地址."""

    def test_declared_relative_icon_first(self):
        """相对图标地址按 base 解析, 优先于站点默认 favicon."""
        html = ('<html><head><base href="/assets/">'
                '<link rel="shortcut icon" href="images/site.png">'
                '</head><body></body></html>')
        assert favicon.favicon_candidates("https://example.com/course", html) == [
            "https://example.com/assets/images/site.png",
            "https://example.com/favicon.ico"]

    def test_no_declaration_uses_origin_fallback(self):
        """没有声明时只回退到同源 /favicon.ico."""
        assert favicon.favicon_candidates(
            "https://example.com/path?x=1", "<html><body></body></html>") == [
                "https://example.com/favicon.ico"]

    def test_non_http_icon_ignored(self):
        """脚本等非 http 图标地址不会进入下载队列."""
        assert favicon.favicon_candidates(
            "https://example.com/", '<link rel="icon" href="javascript:x">') == [
                "https://example.com/favicon.ico"]


class TestStore:
    """缓存、声明下载与失败回退."""

    def test_download_declared_icon_and_reuse_cache(self, tmp_path):
        """网页声明图标只下载一次, 后续读取磁盘缓存."""
        calls = []
        icon = b"\x89PNG\r\n\x1a\nicon"

        def fetch(url, _limit):
            calls.append(url)
            if url.endswith("/logo.png"):
                return icon, "image/png"
            return (b'<link rel="icon" href="/logo.png">', "text/html")

        store = favicon.FaviconStore(str(tmp_path), fetch=fetch)
        first = store.get("https://example.com/course")
        assert first
        with open(first, "rb") as fh:
            assert fh.read() == icon
        assert calls == ["https://example.com/course",
                         "https://example.com/logo.png"]
        assert store.get("https://example.com/course") == first
        assert len(calls) == 2

    def test_direct_image_response(self, tmp_path):
        """直接指向图片的链接无需先解析 HTML."""
        store = favicon.FaviconStore(
            str(tmp_path), fetch=lambda _url, _limit: (b"\x00\x00\x01\x00ico",
                                                        "image/x-icon"))
        assert store.get("https://example.com/favicon.ico")

    def test_html_error_and_negative_cache(self, tmp_path):
        """失败结果 24 小时内不重试, 过期后允许再次获取."""
        calls = []

        def fetch(_url, _limit):
            calls.append(1)
            return b"<html>not found</html>", "text/html"

        now = [1000.0]
        store = favicon.FaviconStore(str(tmp_path), fetch=fetch,
                                     now=lambda: now[0])
        assert store.get("https://example.com") == ""
        assert store.get("https://example.com") == ""
        assert len(calls) == 2
        now[0] += favicon.NEGATIVE_TTL_SECONDS + 1
        assert store.get("https://example.com") == ""
        assert len(calls) == 4

    def test_non_image_fallback_rejected(self, tmp_path):
        """非图片响应不会被保存为 favicon."""
        store = favicon.FaviconStore(
            str(tmp_path), fetch=lambda _url, _limit: (b"not an image",
                                                        "application/octet-stream"))
        assert store.get("https://example.com/favicon.ico") == ""

    def test_network_failure_isolated(self, tmp_path):
        """网络失败返回空且不影响缓存读取."""
        def fetch(_url, _limit):
            raise OSError("offline")

        store = favicon.FaviconStore(str(tmp_path), fetch=fetch)
        assert store.get("https://example.com") == ""
        assert store.cached_icon_path("https://example.com") == ""

    @pytest.mark.parametrize("content_type", ["image/svg+xml", "image/gif"])
    def test_declared_image_type_accepted(self, tmp_path, content_type):
        """服务器声明的图片 MIME 类型可直接缓存."""
        data = b"<svg/>" if "svg" in content_type else b"GIF89a"
        store = favicon.FaviconStore(
            str(tmp_path), fetch=lambda _url, _limit: (data, content_type))
        assert store.get("https://example.com/favicon.svg")
