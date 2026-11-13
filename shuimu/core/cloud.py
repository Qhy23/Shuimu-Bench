"""清华云盘分享链接协议: 匿名读取分享目录, 纯逻辑零 Qt.

分享链接(形如 https://cloud.tsinghua.edu.cn/d/<token>/) 由用户在云盘
网页端创建, 无需登录即可访问. 2026-11-13 实测(见 test/workspace/
cloud_probe.py):

- 目录列举: GET /api/v2.1/share-links/<token>/dirents/?path=<urlencoded>
  匿名可用, 返回 {"dirent_list": [...]}; 不传 path 为根目录; 无效或
  过期 token 返回 404 + {"error_msg": ...}.
- 子目录网页地址: /d/<token>/?p=<urlencoded path>(经系统浏览器打开).
- 分享名: 分享页 <meta property="og:title">.

服务端为 Seafile; 页面/响应形态变化时抛 ProtocolChangedError 提示反馈.
"""

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

CLOUD_ORIGIN = "https://cloud.tsinghua.edu.cn"
CLOUD_HOST = "cloud.tsinghua.edu.cn"
DIRENTS_PATH = "/api/v2.1/share-links/{token}/dirents/"
SHARE_PAGE_PATH = "/d/{token}/"

_TOKEN_RE = re.compile(r"^[0-9a-f]{16,32}$")
_OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]*)"', re.IGNORECASE)

# 目录递归上限(防失控分享库把遍历拖死)
MAX_WALK_DIRS = 1000


class CloudError(Exception):
    """云盘访问失败(链接无效/过期, 服务端错误等), 消息可直接提示用户."""


class ProtocolChangedError(CloudError):
    """云盘服务端页面/响应形态变化, 需要人工适配."""


@dataclass
class ShareRef:
    """解析后的分享链接引用."""

    token: str
    path: str          # 规范化为 / 开头 / 结尾的目录路径, 根为 "/"

    @property
    def root_url(self) -> str:
        """分享根地址(留痕与展示用)."""
        return f"{CLOUD_ORIGIN}{SHARE_PAGE_PATH.format(token=self.token)}"


@dataclass
class CloudDirent:
    """一个目录项(本功能只用到目录, 文件行也保留以备展示)."""

    name: str
    path: str          # 服务端原样返回, 如 /类别/课程/
    is_dir: bool
    size: int = 0
    mtime: str = ""


def normalize_dir_path(path: str) -> str:
    """规范化目录路径: / 开头 / 结尾, 空为根 "/"."""
    text = (path or "").strip()
    if not text:
        return "/"
    if not text.startswith("/"):
        text = "/" + text
    if not text.endswith("/"):
        text += "/"
    return re.sub(r"/{2,}", "/", text)


def parse_share_url(url: str) -> ShareRef:
    """解析分享链接, 非法形态抛 CloudError(不发起网络)."""
    text = (url or "").strip()
    if not text.startswith(("http://", "https://")):
        raise CloudError("不是有效的清华云盘分享链接(须以 http(s):// 开头)")
    parts = urlsplit(text)
    if parts.netloc.lower() != CLOUD_HOST:
        raise CloudError(f"不是清华云盘的链接(域名应为 {CLOUD_HOST})")
    match = re.match(r"^/d/([0-9a-fA-F]+)/?$", parts.path or "")
    if match is None:
        raise CloudError("不是云盘目录分享链接(应为 .../d/<分享id>/ 形式)")
    token = match.group(1).lower()
    if _TOKEN_RE.match(token) is None:
        raise CloudError("分享链接 id 形态异常")
    raw_path = (parse_qs(parts.query).get("p") or ["/"])[0]
    return ShareRef(token=token, path=normalize_dir_path(raw_path))


def subdir_url(token: str, path: str) -> str:
    """目录的网页地址(?p= 形参, 经系统浏览器可直接打开)."""
    encoded = quote(normalize_dir_path(path), safe="")
    return (f"{CLOUD_ORIGIN}{SHARE_PAGE_PATH.format(token=token)}"
            f"?p={encoded}")


def _quote_path(path: str) -> str:
    """API path 参数编码(斜杠也编码, 2026-11-13 实测服务端接受)."""
    return quote(normalize_dir_path(path), safe="")


class CloudClient:
    """云盘分享链接客户端(匿名, 无需登录态)."""

    def __init__(self, transport):
        """transport 为 campus_net.Transport 实现(生产 urllib, 测试 Memory)."""
        self._transport = transport

    def _get(self, url: str) -> str:
        """发起 GET 并返回文本; 非 200 统一转 CloudError."""
        response = self._transport.fetch(url)
        if response.status == 404:
            raise CloudError("分享链接不存在或已过期")
        if response.status != 200:
            raise CloudError(f"云盘返回错误(HTTP {response.status})")
        return response.text

    def _get_json(self, url: str) -> dict:
        """GET 并按 JSON 解析; 形态不符抛 ProtocolChangedError."""
        text = self._get(url)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ProtocolChangedError("云盘响应不是 JSON") from exc
        if not isinstance(data, dict):
            raise ProtocolChangedError("云盘响应形态变化(非对象)")
        return data

    def dirents(self, token: str, path: str = "/") -> list:
        """列目录(匿名); 返回 CloudDirent 列表, 目录在前文件在后(服务端序)."""
        url = f"{CLOUD_ORIGIN}{DIRENTS_PATH.format(token=token)}"
        if normalize_dir_path(path) != "/":
            url += f"?path={_quote_path(path)}"
        data = self._get_json(url)
        rows = data.get("dirent_list")
        if not isinstance(rows, list):
            raise ProtocolChangedError("云盘响应缺 dirent_list")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProtocolChangedError("云盘目录项形态变化")
            is_dir = bool(row.get("is_dir"))
            name = row.get("folder_name") if is_dir else row.get("file_name")
            full = row.get("folder_path") if is_dir else row.get("file_path")
            if not isinstance(name, str) or not isinstance(full, str):
                raise ProtocolChangedError("云盘目录项缺名称/路径字段")
            try:
                size = int(row.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            mtime = row.get("last_modified")
            result.append(CloudDirent(
                name=name, path=full, is_dir=is_dir, size=size,
                mtime=str(mtime or "")))
        return result

    def share_title(self, token: str) -> str:
        """分享页 og:title(分享库显示名); 取不到返回空串(宽容, 非关键)."""
        text = self._get(f"{CLOUD_ORIGIN}{SHARE_PAGE_PATH.format(token=token)}")
        match = _OG_TITLE_RE.search(text)
        return match.group(1).strip() if match else ""

    def walk_dirs(self, token: str, root: str = "/",
                  max_depth: int = 2) -> list:
        """递归收集目录(不含文件), 含 root 的直接子目录至 max_depth 层.

        max_depth=1: 仅根下子目录; 2: 再深入一层(多课程资料库的常见形态).
        目录总数超 MAX_WALK_DIRS 抛 CloudError.
        """
        found = []
        queue = [(normalize_dir_path(root), 1)]
        while queue:
            current, depth = queue.pop(0)
            for item in self.dirents(token, current):
                if not item.is_dir:
                    continue
                found.append(item)
                if len(found) > MAX_WALK_DIRS:
                    raise CloudError("分享库目录过多, 请提高目录层级后再试")
                if depth < max_depth:
                    queue.append((item.path, depth + 1))
        return found
