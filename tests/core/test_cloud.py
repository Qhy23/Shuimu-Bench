"""cloud.py 单元测试: 分享链接协议(假 transport 全离线, 零真实账号)."""

import json
from urllib.parse import quote

import pytest

from shuimu.core.campus_net import MemoryTransport
from shuimu.core.cloud import (
    CLOUD_ORIGIN,
    CloudClient,
    CloudError,
    ProtocolChangedError,
    normalize_dir_path,
    parse_share_url,
    subdir_url,
)

TOKEN = "0123456789abcdef0123"
SHARE_URL = f"{CLOUD_ORIGIN}/d/{TOKEN}/"
SHARE_PAGE = f"{CLOUD_ORIGIN}/d/{TOKEN}/"
DIRENTS_API = f"{CLOUD_ORIGIN}/api/v2.1/share-links/{TOKEN}/dirents/"

PAGE_HTML = """<!DOCTYPE html>
<html><head><title>清华大学云盘</title>
<meta property="og:title" content="学习资料库" />
</head><body></body></html>
"""


def _q(path: str) -> str:
    """与 cloud._quote_path 同款编码."""
    return quote(path, safe="")


def add_share_page(transport, title="学习资料库", status=200):
    """分享页(og:title)."""
    transport.add("GET", SHARE_PAGE,
                  status=status, text=PAGE_HTML.replace("学习资料库", title))


def add_dirents(transport, rows, path=None, status=200, text=None):
    """目录列举端点; path 为 None 表示根(不带查询串)."""
    url = DIRENTS_API if path is None else \
        f"{DIRENTS_API}?path={_q(path)}"
    if text is None:
        text = json.dumps({"dirent_list": rows})
    transport.add("GET", url, status=status, text=text)


def dir_row(name, parent="/"):
    """构造一个目录行(与服务端实测形态一致)."""
    return {"size": 0, "last_modified": "2025-03-17T12:40:54+08:00",
            "is_dir": True, "folder_path": f"{parent}{name}/",
            "folder_name": name}


def file_row(name, parent="/", size=97):
    """构造一个文件行."""
    return {"size": size, "last_modified": "2025-03-17T12:40:54+08:00",
            "is_dir": False, "file_path": f"{parent}{name}",
            "file_name": name}


class TestParseShareUrl:
    """分享链接解析(纯本地)."""

    def test_root(self):
        """根链接."""
        ref = parse_share_url(SHARE_URL)
        assert ref.token == TOKEN
        assert ref.path == "/"
        assert ref.root_url == SHARE_URL

    def test_no_trailing_slash(self):
        """末尾无斜杠也接受, 规范化为根."""
        ref = parse_share_url(f"{CLOUD_ORIGIN}/d/{TOKEN}")
        assert ref.token == TOKEN
        assert ref.path == "/"

    def test_uppercase_token_normalized(self):
        """大写 token 转小写."""
        ref = parse_share_url(f"{CLOUD_ORIGIN}/d/{TOKEN.upper()}/")
        assert ref.token == TOKEN

    def test_subdir_p_param(self):
        """?p= 子目录路径归一化."""
        ref = parse_share_url(f"{SHARE_URL}?p=%2F%E7%B1%BB%E5%88%ABA%2F")
        assert ref.token == TOKEN
        assert ref.path == "/类别A/"

    def test_p_param_without_slashes(self):
        """?p= 无首尾斜杠自动补齐."""
        ref = parse_share_url(f"{SHARE_URL}?p=%E7%B1%BB%E5%88%ABA")
        assert ref.path == "/类别A/"

    def test_not_http(self):
        """非 http(s) 拒绝."""
        with pytest.raises(CloudError):
            parse_share_url("ftp://cloud.tsinghua.edu.cn/d/" + TOKEN + "/")

    def test_wrong_host(self):
        """非清华云盘域名拒绝."""
        with pytest.raises(CloudError):
            parse_share_url(f"https://example.com/d/{TOKEN}/")

    def test_not_share_path(self):
        """非 /d/ 分享形态拒绝."""
        with pytest.raises(CloudError):
            parse_share_url(f"{CLOUD_ORIGIN}/home/my/dir/")

    def test_bad_token(self):
        """token 形态异常拒绝."""
        with pytest.raises(CloudError):
            parse_share_url(f"{CLOUD_ORIGIN}/d/not-a-token!/")

    def test_empty(self):
        """空串拒绝."""
        with pytest.raises(CloudError):
            parse_share_url("  ")


class TestNormalizeDirPath:
    """目录路径规范化."""

    def test_variants(self):
        """空/无斜杠/多斜杠."""
        assert normalize_dir_path("") == "/"
        assert normalize_dir_path("/a/b") == "/a/b/"
        assert normalize_dir_path("a/b/") == "/a/b/"
        assert normalize_dir_path("//a//b//") == "/a/b/"


class TestSubdirUrl:
    """子目录网页地址."""

    def test_quoting(self):
        """中文与斜杠全部编码."""
        url = subdir_url(TOKEN, "/类别A/课程一/")
        assert url == (f"{SHARE_URL}"
                       f"?p={_q('/类别A/课程一/')}")


class TestDirents:
    """目录列举."""

    def test_root_dirs_and_files(self):
        """根目录: 目录与文件都解析."""
        transport = MemoryTransport()
        add_share_page(transport)
        add_dirents(transport, [dir_row("类别A"), file_row("README.md")])
        client = CloudClient(transport)
        rows = client.dirents(TOKEN)
        assert [(r.name, r.path, r.is_dir) for r in rows] == [
            ("类别A", "/类别A/", True),
            ("README.md", "/README.md", False)]
        assert rows[1].size == 97
        assert rows[0].mtime.startswith("2025-03-17")

    def test_subdir_query(self):
        """子目录带 ?path= 查询."""
        transport = MemoryTransport()
        add_dirents(transport, [dir_row("课程一", parent="/类别A/")],
                    path="/类别A/")
        client = CloudClient(transport)
        rows = client.dirents(TOKEN, "/类别A/")
        assert rows[0].path == "/类别A/课程一/"

    def test_root_omits_query(self):
        """根目录请求不带查询串."""
        transport = MemoryTransport()
        add_dirents(transport, [])
        client = CloudClient(transport)
        client.dirents(TOKEN)
        assert transport.requests[0][1] == DIRENTS_API

    def test_404_means_missing(self):
        """无效/过期链接: 404 -> CloudError."""
        transport = MemoryTransport()
        add_dirents(transport, [], status=404,
                    text=json.dumps({"error_msg": "Share link not found."}))
        client = CloudClient(transport)
        with pytest.raises(CloudError, match="不存在或已过期"):
            client.dirents(TOKEN)

    def test_500_error(self):
        """服务端 5xx 统一提示."""
        transport = MemoryTransport()
        add_dirents(transport, [], status=500, text="boom")
        client = CloudClient(transport)
        with pytest.raises(CloudError, match="HTTP 500"):
            client.dirents(TOKEN)

    def test_non_json(self):
        """非 JSON 响应 -> ProtocolChangedError."""
        transport = MemoryTransport()
        add_dirents(transport, [], text="<html>oops</html>")
        client = CloudClient(transport)
        with pytest.raises(ProtocolChangedError):
            client.dirents(TOKEN)

    def test_missing_dirent_list(self):
        """缺 dirent_list -> ProtocolChangedError."""
        transport = MemoryTransport()
        add_dirents(transport, [], text=json.dumps({"other": []}))
        client = CloudClient(transport)
        with pytest.raises(ProtocolChangedError, match="dirent_list"):
            client.dirents(TOKEN)

    def test_row_missing_fields(self):
        """目录项缺字段 -> ProtocolChangedError."""
        transport = MemoryTransport()
        add_dirents(transport, [{"is_dir": True, "folder_name": "x"}])
        client = CloudClient(transport)
        with pytest.raises(ProtocolChangedError, match="缺名称"):
            client.dirents(TOKEN)


class TestShareTitle:
    """分享名抓取."""

    def test_title_found(self):
        """og:title 命中."""
        transport = MemoryTransport()
        add_share_page(transport, title="多课程资料库")
        assert CloudClient(transport).share_title(TOKEN) == "多课程资料库"

    def test_title_missing_tolerated(self):
        """缺 og:title 宽容为空串."""
        transport = MemoryTransport()
        transport.add("GET", SHARE_PAGE, text="<html></html>")
        assert CloudClient(transport).share_title(TOKEN) == ""

    def test_page_404(self):
        """分享页 404 -> CloudError."""
        transport = MemoryTransport()
        add_share_page(transport, status=404)
        with pytest.raises(CloudError):
            CloudClient(transport).share_title(TOKEN)


class TestWalkDirs:
    """目录递归收集."""

    def _seed_tree(self, transport):
        """两级树: 根 -> 类别A/类别B -> 课程."""
        add_dirents(transport, [dir_row("类别A"), dir_row("类别B")])
        add_dirents(transport, [dir_row("课程一", parent="/类别A/")],
                    path="/类别A/")
        add_dirents(transport, [dir_row("课程二", parent="/类别B/"),
                                file_row("说明.txt", parent="/类别B/")],
                    path="/类别B/")

    def test_depth_one(self):
        """深度 1: 仅根下子目录."""
        transport = MemoryTransport()
        self._seed_tree(transport)
        dirs = CloudClient(transport).walk_dirs(TOKEN, max_depth=1)
        assert [d.name for d in dirs] == ["类别A", "类别B"]

    def test_depth_two(self):
        """深度 2: 含孙目录, 文件不入列."""
        transport = MemoryTransport()
        self._seed_tree(transport)
        dirs = CloudClient(transport).walk_dirs(TOKEN, max_depth=2)
        assert [d.name for d in dirs] == ["类别A", "类别B",
                                          "课程一", "课程二"]

    def test_order_breadth_first(self):
        """广度优先: 同层先于深层."""
        transport = MemoryTransport()
        self._seed_tree(transport)
        dirs = CloudClient(transport).walk_dirs(TOKEN, max_depth=2)
        names = [d.name for d in dirs]
        assert names.index("类别B") < names.index("课程一")

    def test_runaway_capped(self):
        """目录总数超限 -> CloudError."""
        transport = MemoryTransport()
        many = [dir_row(f"目录{i:02d}") for i in range(50)]
        add_dirents(transport, many)
        for index in range(50):
            # 每个根下目录再放一批子目录, 总量很快破上限
            add_dirents(transport, many, path=f"/目录{index:02d}/")
        client = CloudClient(transport)
        with pytest.raises(CloudError, match="目录过多"):
            client.walk_dirs(TOKEN, max_depth=2)
