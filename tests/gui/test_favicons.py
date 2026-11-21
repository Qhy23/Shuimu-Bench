"""favicon 后台服务与链接页的回退/缓存行为."""

from shuimu.core.quicklinks import QuickLink
from shuimu.gui import file_icons
from shuimu.gui.favicons import FaviconService
from shuimu.gui.links_page import LinksPage


class FakeStore:
    """可控的 favicon 缓存替身, 不发生网络请求."""

    def __init__(self, cached="", result=""):
        self.cached = cached
        self.result = result
        self.calls = []

    def cached_icon_path(self, _url):
        """返回预设缓存路径."""
        return self.cached

    def get(self, url):
        """记录后台获取并返回预设结果."""
        self.calls.append(url)
        return self.result


class FakeLinksController:
    """只提供链接清单的控制器替身."""

    def __init__(self, links):
        self.links = links

    def list_quick_links(self):
        """返回预设链接."""
        return self.links


def test_disabled_service_does_not_fetch():
    """默认关闭时不排队、不发起网络获取."""
    store = FakeStore()
    service = FaviconService(store)
    service.request("https://example.com")
    assert not service.is_enabled()
    assert not store.calls


def test_cached_icon_available_offline():
    """有缓存时离线立即回报, 不需要启用联网."""
    store = FakeStore(cached="/tmp/example.ico")
    service = FaviconService(store)
    seen = []
    service.icon_ready.connect(lambda url, path: seen.append((url, path)))
    service.request("https://example.com")
    assert seen == [("https://example.com", "/tmp/example.ico")]
    assert not store.calls


def test_enabled_service_fetches_and_deduplicates(qtbot):
    """启用后同 URL 多次请求只获取一次."""
    store = FakeStore(result="/tmp/example.ico")
    service = FaviconService(store)
    seen = []
    service.icon_ready.connect(lambda url, path: seen.append((url, path)))
    service.set_enabled(True)
    service.request("https://example.com")
    service.request("https://example.com")
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen == [("https://example.com", "/tmp/example.ico")]
    assert store.calls == ["https://example.com"]
    service.shutdown()


def test_disable_discards_late_result(qtbot):
    """关闭后丢弃旧任务的晚到结果."""
    store = FakeStore(result="/tmp/example.ico")
    service = FaviconService(store)
    seen = []
    service.icon_ready.connect(lambda url, path: seen.append((url, path)))
    service.set_enabled(True)
    service.request("https://example.com")
    service.set_enabled(False)
    qtbot.waitUntil(lambda: not service._running, timeout=5000)
    assert not seen
    service.shutdown()


def test_links_page_replaces_default_with_cached_favicon(qtbot, tmp_path):
    """真实 PNG 缓存就绪后替换默认 WEB 图标."""
    path = tmp_path / "site.png"
    file_icons._draw_type_icon("OK", "#3fb950", 16).save(str(path))
    store = FakeStore(cached=str(path))
    service = FaviconService(store)
    page = LinksPage(FakeLinksController([QuickLink("站点", "https://example.com")]),
                     favicon_service=service)
    qtbot.addWidget(page)
    leaf = page.tree.topLevelItem(0).child(0)
    assert not leaf.icon(0).isNull()
    assert leaf.icon(0).availableSizes()
    service.shutdown()


def test_links_page_defaults_to_web_icon_without_network(qtbot):
    """链接页无缓存时显示默认 WEB 图标且不联网."""
    store = FakeStore()
    service = FaviconService(store)
    page = LinksPage(FakeLinksController([QuickLink("站点", "https://example.com")]),
                     favicon_service=service)
    qtbot.addWidget(page)
    leaf = page.tree.topLevelItem(0).child(0)
    assert not leaf.icon(0).isNull()
    assert not store.calls
    service.shutdown()
