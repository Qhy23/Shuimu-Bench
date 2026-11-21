"""网页 favicon 的后台加载, 不阻塞 Qt 主线程.

只有显式 set_enabled(True) 后才允许网络获取; 默认仅显示默认 WEB 图标
和已有磁盘缓存. 同站点多处链接复用一个任务, 结果以 URL 回调通知视图.
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from shuimu.core.favicon import FaviconStore

# 工作线程只持有此数据通道; 服务关闭后不再向已销毁的 Qt 对象发信号.


class _WorkerSignals(QObject):
    finished = Signal(str, str, int)  # url, icon_path(空为失败), generation


class _FetchJob(QRunnable):
    def __init__(self, store, url: str, generation: int):
        super().__init__()
        self._store = store
        self._url = url
        self._generation = generation
        self.signals = _WorkerSignals()

    def run(self) -> None:
        path = self._store.get(self._url)
        self.signals.finished.emit(self._url, path, self._generation)


class FaviconService(QObject):
    """单线程后台 favicon 队列, 生命周期由主窗口持有."""

    icon_ready = Signal(str, str)  # url, icon_path

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self.store = store or FaviconStore()
        self._enabled = False
        self._generation = 0
        self._queue = []
        self._queued = set()
        self._requested = set()  # 同 URL 已登记后台获取, 防止缓存回报后重复请求
        self._running = False
        self._worker = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)  # 单后台线程, 不阻塞 UI, 站点请求串行

    def set_enabled(self, enabled: bool) -> None:
        """切换联网授权; 关闭后不再取新任务, 已完成结果仍可更新缓存."""
        self._enabled = enabled
        self._generation += 1
        self._queue.clear()
        self._queued.clear()
        self._requested.clear()
        if enabled:
            self._pump()

    def is_enabled(self) -> bool:
        """当前是否允许后台联网获取."""
        return self._enabled

    def request(self, url: str) -> None:
        """登记一条链接; 已缓存立即回报, 未缓存只在启用后排队."""
        cached = self.store.cached_icon_path(url)
        if cached:
            self.icon_ready.emit(url, cached)
            return
        if not self._enabled or url in self._queued or url in self._requested:
            return
        self._queue.append(url)
        self._queued.add(url)
        self._requested.add(url)  # 同一会话同 URL 最多后台获取一次
        self._pump()

    def _pump(self) -> None:
        if not self._enabled or self._running or not self._queue:
            return
        url = self._queue.pop(0)
        self._queued.discard(url)
        self._running = True
        self._worker = _FetchJob(self.store, url, self._generation)
        self._worker.signals.finished.connect(self._finished)
        self._pool.start(self._worker)

    def _finished(self, url: str, path: str, generation: int) -> None:
        self._running = False
        self._worker = None
        if generation == self._generation and path:
            self.icon_ready.emit(url, path)
        self._pump()

    def shutdown(self) -> None:
        """禁止后续网络任务, 有界等待后台请求结束."""
        self.set_enabled(False)
        self._pool.waitForDone(5000)
