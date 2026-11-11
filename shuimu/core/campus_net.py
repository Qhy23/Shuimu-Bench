"""校园服务 HTTP 传输层: 单跳 Transport 抽象, urllib 实现, 手动 CookieJar.

设计: Transport 只做"一跳"请求 -- 不跟重定向, 不管理 cookie; 重定向
跟随(<=10 跳, 301/302/303 的 POST 降级 GET)与 Cookie 注入/回收由协议层
(shuimu/core/campus.py) 控制. 这样协议层可用 MemoryTransport 按 URL
分发假响应做离线测试(不联网), 生产换 UrllibTransport 即可.

协议参考自 thu-info-app 的 thu-info-lib(BSL-1.1)行为, 代码自写.
"""

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/79.0.3945.88 Safari/537.36")


class TransportError(OSError):
    """网络层故障(DNS/超时/TLS 等), 由调用方转成 network_error 提示."""


@dataclass
class HttpResponse:
    """单跳响应; headers 保序保留重复(多个 Set-Cookie).

    content 为响应体原始字节(二进制下载用); text 是同一字节的解码视图,
    既有按 text 断言的调用方行为不变.
    """

    status: int
    headers: list = field(default_factory=list)
    text: str = ""
    url: str = ""
    content: bytes = b""

    def header(self, name: str):
        """取字段最后值(大小写不敏感); 无则 None."""
        wanted = name.lower()
        for key, value in reversed(self.headers):
            if key.lower() == wanted:
                return value
        return None

    def headers_all(self, name: str) -> list:
        """取字段全部值(按出现顺序)."""
        wanted = name.lower()
        return [value for key, value in self.headers
                if key.lower() == wanted]


def encode_form(pairs: list) -> str:
    """表单编码(保持字段顺序; 键值即登录协议要求的提交序)."""
    return urlencode(pairs)


class Transport:
    """单跳 HTTP 抽象(生产与测试各一实现)."""

    def fetch(self, url: str, method: str = "GET",
              body: str = None, headers: dict = None) -> HttpResponse:
        """发起一次请求并返回响应; 由子类实现."""
        raise NotImplementedError


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁用自动重定向: 30x 响应原样返回, 由协议层手动跟."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibTransport(Transport):
    """生产实现: urllib.request, 单跳, 尊重代理环境变量, 15s 超时."""

    def __init__(self, timeout: int = 15):
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def fetch(self, url: str, method: str = "GET",
              body: str = None, headers: dict = None) -> HttpResponse:
        request_headers = {"User-Agent": USER_AGENT}
        if body is not None:
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded")
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url, data=body.encode("utf-8") if body is not None else None,
            headers=request_headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as resp:
                return self._to_response(resp, url)
        except urllib.error.HTTPError as exc:   # 30x/4xx/5xx 都到这
            try:
                return self._to_response(exc, url)
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(str(exc)) from exc

    @staticmethod
    def _to_response(resp, url: str) -> HttpResponse:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return HttpResponse(
            status=resp.status, headers=list(resp.headers.items()),
            text=raw.decode(charset, errors="replace"), url=url, content=raw)


class MemoryTransport(Transport):
    """测试实现: 按 (method, url) 精确匹配优先于前缀匹配, 记录请求序列.

    同一 (method, url) 注册多条时按注册顺序依次消费(全部消费完再复用
    最后一条) -- 覆盖登录协议里 check 地址被提交两次等时序场景.
    """

    def __init__(self):
        self._routes = []      # [method, url, HttpResponse, 已消费]
        self.requests = []     # [(method, url, body, headers)]

    def add(self, method: str, url: str, status: int = 200, text: str = "",
            headers: list = None, location: str = None,
            content: bytes = None) -> None:
        """注册一条响应; location 非空等价加 Location 头.

        content 给定时以原始字节为准: 未显式给 text 时 text 视为其
        utf-8 解码视图; 只给 text 时 content 为其 utf-8 编码(往返一致).
        """
        all_headers = list(headers or [])
        if location is not None:
            all_headers.append(("Location", location))
        if content is None:
            content = text.encode("utf-8")
        elif not text:
            text = content.decode("utf-8", errors="replace")
        self._routes.append(
            [method, url, HttpResponse(
                status, all_headers, text, url, content), False])

    def fetch(self, url: str, method: str = "GET",
              body: str = None, headers: dict = None) -> HttpResponse:
        self.requests.append((method, url, body, dict(headers or {})))
        exact = [r for r in self._routes if r[0] == method and r[1] == url]
        prefix = [r for r in self._routes if r[0] == method
                  and r[1] != url and url.startswith(r[1])]
        matched = exact or prefix
        if not matched:
            raise AssertionError(f"MemoryTransport 无路由: {method} {url}")
        route = next((r for r in matched if not r[3]), matched[-1])
        route[3] = True
        stored = route[2]
        return HttpResponse(stored.status, list(stored.headers),
                            stored.text, url, stored.content)


class CookieJar:
    """手动 cookie 管理: {host: {name: value}}, 精确 host 匹配."""

    def __init__(self):
        self._hosts = {}

    def store_from_response(self, url: str, response: HttpResponse) -> None:
        """收响应的全部 Set-Cookie 到该 URL 的 host."""
        host = urlsplit(url).netloc
        bucket = self._hosts.setdefault(host, {})
        for value in response.headers_all("Set-Cookie"):
            pair = value.split(";", 1)[0]
            if "=" not in pair:
                continue
            name, content = pair.split("=", 1)
            bucket[name.strip()] = content.strip()

    def header_for(self, url: str) -> str:
        """拼该 URL host 的 Cookie 头值; 空则空串."""
        bucket = self._hosts.get(urlsplit(url).netloc, {})
        return "; ".join(f"{name}={value}" for name, value in bucket.items())

    def as_dict(self) -> dict:
        """序列化视图(深拷贝)."""
        return {host: dict(bucket)
                for host, bucket in self._hosts.items() if bucket}

    def load(self, data: dict) -> None:
        """从序列化视图恢复."""
        self._hosts = {host: dict(bucket)
                       for host, bucket in (data or {}).items()}

    def clear(self) -> None:
        """清空全部 cookie."""
        self._hosts = {}
