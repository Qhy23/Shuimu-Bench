"""网页 favicon 的按需获取与本地缓存(纯逻辑, 零 Qt).

只访问目标站点网页声明的图标, 不使用第三方图标服务. 获取由 GUI 在
用户明确开启后于后台线程调用; 缺省关闭, 不影响核心离线功能. 缓存
只保存公开图标, 不保存 Cookie、网页正文或任何凭据.
"""

import hashlib
import html.parser
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from shuimu.core.paths import default_favicon_cache_dir

MAX_HTML_BYTES = 512 * 1024
MAX_ICON_BYTES = 512 * 1024
TIMEOUT_SECONDS = 4
NEGATIVE_TTL_SECONDS = 24 * 60 * 60
USER_AGENT = "ShuimuBench/0.1 favicon-fetcher"


class _IconLinkParser(html.parser.HTMLParser):
    """从 HTML head 中提取 favicon 候选(不执行脚本)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.in_head = True

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "body":
            self.in_head = False
        if tag == "base" and values.get("href"):
            self.links.append(("base", values["href"]))
        if not self.in_head or tag != "link":
            return
        rel = values.get("rel", "").lower().split()
        if any(token in rel for token in ("icon", "shortcut")):
            href = values.get("href", "").strip()
            if href:
                self.links.append(("icon", href))


def favicon_candidates(page_url: str, html_text: str) -> list:
    """按网页声明 -> /favicon.ico 的顺序返回 http(s) 图标 URL."""
    parser = _IconLinkParser()
    try:
        parser.feed(html_text)
    except (ValueError, html.parser.HTMLParseError):
        pass
    base = page_url
    candidates = []
    for kind, href in parser.links:
        if kind == "base":
            base = urllib.parse.urljoin(page_url, href)
            continue
        url = urllib.parse.urljoin(base, href)
        if url.startswith(("http://", "https://")):
            candidates.append(url)
    origin = urllib.parse.urlsplit(page_url)
    if origin.scheme in ("http", "https") and origin.netloc:
        candidates.append(f"{origin.scheme}://{origin.netloc}/favicon.ico")
    return list(dict.fromkeys(candidates))


class FaviconStore:
    """公开 favicon 的磁盘缓存; transport 可注入, 测试全离线."""

    def __init__(self, cache_dir: str = None, fetch=None, now=time.time):
        self.cache_dir = cache_dir or default_favicon_cache_dir()
        self._fetch = fetch or self._download
        self._now = now

    def _paths(self, url: str) -> tuple:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return (os.path.join(self.cache_dir, digest + ".icon"),
                os.path.join(self.cache_dir, digest + ".json"))

    def cached_icon_path(self, url: str) -> str:
        """已有有效图标返回缓存路径, 否则空串; 不触发联网."""
        icon_path, _meta_path = self._paths(url)
        if os.path.isfile(icon_path) and os.path.getsize(icon_path) > 0:
            return icon_path
        return ""

    def get(self, url: str) -> str:
        """获取图标缓存路径; 无有效图标/失败返回空串."""
        icon_path, meta_path = self._paths(url)
        if os.path.isfile(icon_path) and os.path.getsize(icon_path) > 0:
            return icon_path
        meta = self._read_meta(meta_path)
        failed_at = meta.get("failed_at", 0)
        if failed_at and failed_at + NEGATIVE_TTL_SECONDS > self._now():
            return ""
        try:
            data, content_type = self._fetch_icon(url)
        except (OSError, ValueError, urllib.error.URLError):
            self._write_meta(meta_path, {"failed_at": self._now()})
            return ""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(icon_path, "wb") as fh:
                fh.write(data)
            self._write_meta(meta_path, {"content_type": content_type,
                                         "cached_at": self._now()})
        except OSError:
            return ""
        return icon_path

    def _fetch_icon(self, url: str) -> tuple:
        data, content_type = self._fetch(url, MAX_HTML_BYTES)
        if content_type.startswith("image/"):
            return data, content_type
        text = data.decode("utf-8", errors="replace")
        errors = []
        for icon_url in favicon_candidates(url, text):
            try:
                icon_data, icon_type = self._fetch(icon_url, MAX_ICON_BYTES)
            except (OSError, ValueError, urllib.error.URLError) as exc:
                errors.append(str(exc))
                continue
            if icon_type.startswith("image/") or _looks_like_icon(icon_data):
                return icon_data, icon_type
            errors.append(f"非图片: {icon_type}")
        raise ValueError("未找到网页 favicon: " + "; ".join(errors[:2]))

    @staticmethod
    def _download(url: str, max_bytes: int) -> tuple:
        if not url.startswith(("http://", "https://")):
            raise ValueError("仅允许 http(s) URL")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            content_type = resp.headers.get_content_type()
            data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("响应超过大小上限")
        return data, content_type

    @staticmethod
    def _read_meta(path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as fh:
                value = json.load(fh)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_meta(path: str, meta: dict) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(meta, fh)
        except OSError:
            pass  # 缓存不可写不阻断界面


def _looks_like_icon(data: bytes) -> bool:
    """常见 favicon 魔数(ICO/PNG), 避免把错误 HTML 当图片缓存."""
    return (data.startswith(b"\x00\x00\x01\x00")
            or data.startswith(b"\x89PNG\r\n\x1a\n"))
