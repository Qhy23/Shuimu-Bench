"""campus_net.py 单元测试: 响应对象, CookieJar, 假 transport 与 urllib 单跳."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from shuimu.core.campus_net import (
    CookieJar,
    HttpResponse,
    MemoryTransport,
    TransportError,
    UrllibTransport,
    encode_form,
)


class TestHttpResponse:
    """响应头访问."""

    def test_header_last_and_case_insensitive(self):
        """header 取最后值且大小写不敏感."""
        response = HttpResponse(200, [("Set-Cookie", "a=1"),
                                       ("set-cookie", "b=2")])
        assert response.header("SET-COOKIE") == "b=2"
        assert response.headers_all("set-cookie") == ["a=1", "b=2"]
        assert response.header("missing") is None


class TestEncodeForm:
    """表单编码."""

    def test_order_preserved_and_quoted(self):
        """字段顺序保持且特殊字符转义."""
        body = encode_form([("i_user", "20231234567"),
                            ("i_pass", "04abc+/="),
                            ("deviceName", "水木工作台")])
        assert body.startswith("i_user=20231234567&i_pass=04")
        assert "deviceName=" in body


class TestCookieJar:
    """手动 CookieJar."""

    def _response(self, *cookies):
        return HttpResponse(200, [("Set-Cookie", c) for c in cookies])

    def test_store_and_header_for(self):
        """同 host 收发; 属性段被丢弃."""
        jar = CookieJar()
        jar.store_from_response(
            "https://id.tsinghua.edu.cn/do/x",
            self._response("SESSION=abc; Path=/; HttpOnly", "JSESSION=dd"))
        assert jar.header_for("https://id.tsinghua.edu.cn/other") == \
            "SESSION=abc; JSESSION=dd"

    def test_other_host_isolated(self):
        """不同 host 互不可见."""
        jar = CookieJar()
        jar.store_from_response("https://a.tsinghua.edu.cn/",
                                self._response("S=1"))
        assert jar.header_for("https://b.tsinghua.edu.cn/") == ""

    def test_roundtrip_and_clear(self):
        """as_dict/load 往返与清空."""
        jar = CookieJar()
        jar.store_from_response("https://webvpn.tsinghua.edu.cn/",
                                self._response("wengine=1"))
        data = jar.as_dict()
        fresh = CookieJar()
        fresh.load(data)
        assert fresh.header_for("https://webvpn.tsinghua.edu.cn/") == \
            "wengine=1"
        jar.clear()
        assert jar.as_dict() == {}


class TestMemoryTransport:
    """假 transport 分发与记录."""

    def test_exact_before_prefix(self):
        """精确匹配优先于前缀."""
        transport = MemoryTransport()
        transport.add("GET", "https://x/", text="prefix")
        transport.add("GET", "https://x/exact", text="exact")
        assert transport.fetch("https://x/exact").text == "exact"

    def test_sequential_consumption(self):
        """同 URL 多条按注册顺序依次消费."""
        transport = MemoryTransport()
        transport.add("POST", "https://x/check", text="first")
        transport.add("POST", "https://x/check", text="second")
        assert transport.fetch("https://x/check", "POST", body="a").text \
            == "first"
        assert transport.fetch("https://x/check", "POST", body="b").text \
            == "second"

    def test_requests_recorded_with_headers(self):
        """请求序列记录 method/url/body/headers."""
        transport = MemoryTransport()
        transport.add("GET", "https://x/")
        transport.fetch("https://x/", headers={"Cookie": "a=1"})
        assert transport.requests == [
            ("GET", "https://x/", None, {"Cookie": "a=1"})]

    def test_no_route_raises(self):
        """无路由断言失败(测试自身的脚手架错误)."""
        with pytest.raises(AssertionError):
            MemoryTransport().fetch("https://nowhere/")


class TestUrllibTransport:
    """urllib 单跳行为(本地 http.server)."""

    @pytest.fixture
    def server(self):
        """起一个回 302 + Set-Cookie 的本地服务, 返回 (transport, url)."""
        class Handler(BaseHTTPRequestHandler):
            """任何 GET 都回 302 + Set-Cookie."""

            def do_GET(self):  # pylint: disable=invalid-name
                """http.server 的路由约定名."""
                self.send_response(302)
                self.send_header("Location", "https://example.invalid/next")
                self.send_header("Set-Cookie", "SESSION=xyz; Path=/")
                self.end_headers()

            def log_message(self, *_args):
                """静默访问日志."""
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        yield UrllibTransport(), \
            f"http://127.0.0.1:{httpd.server_port}/login"
        httpd.shutdown()

    def test_single_hop_no_redirect(self, server):
        """302 不被自动跟随, 状态/Location/Set-Cookie 原样可见."""
        transport, url = server
        jar = CookieJar()
        response = transport.fetch(url)
        jar.store_from_response(url, response)
        assert response.status == 302
        assert response.header("Location") == "https://example.invalid/next"
        assert jar.header_for(url) == "SESSION=xyz"

    def test_network_error_wrapped(self, server):
        """不可达地址转 TransportError."""
        transport, _ = server
        with pytest.raises(TransportError):
            transport.fetch("https://no-such-host.invalid/x")
