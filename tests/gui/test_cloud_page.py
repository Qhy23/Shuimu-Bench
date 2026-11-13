"""cloud_page 测试: 两态/浏览/挂载(offscreen, 假 controller + 真临时库)."""

from types import SimpleNamespace

from shuimu.core.actions import ActionResult
from shuimu.core.storage import Storage
from shuimu.gui.cloud_page import (
    PAGE_DISABLED,
    PAGE_MAIN,
    CloudPage,
)

SHARE_URL = "https://cloud.tsinghua.edu.cn/d/0123456789abcdef0123/"

BROWSE_DETAIL = {
    "share": "多课程资料库",
    "token": "0123456789abcdef0123",
    "mounts": [{"path": "/专业核心课程/核仪器原理/", "name": "核仪器原理",
                "url": SHARE_URL + "?p=x", "course_id": 2,
                "course_name": "核仪器原理"}],
    "unmatched": ["其他"],
}

MOUNT_DETAIL = {
    "share": "多课程资料库",
    "token": "0123456789abcdef0123",
    "depth": 2,
    "mounted": [{"course": "核仪器原理", "dir": "核仪器原理",
                 "url": SHARE_URL + "?p=x", "resource_id": 7}],
    "skipped": [],
    "unmatched": ["其他"],
}


def make_record(status="ok"):
    """cloud 插件记录替身."""
    return SimpleNamespace(status=status, error="",
                           manifest=SimpleNamespace(name="清华云盘"))


class FakeController:
    """CloudPage 依赖的 controller 替身(动作记录, 零网络)."""

    def __init__(self, storage, status="ok", browse_ok=True):
        self.storage = storage
        self.record = make_record(status)
        self.browse_ok = browse_ok
        self.plugins = SimpleNamespace(get=lambda _pid: self.record)
        self.browse_calls = []
        self.mount_calls = []
        self.enabled_calls = []

    def cloud_browse(self, url, max_depth=2):
        """浏览替身."""
        self.browse_calls.append((url, max_depth))
        if not self.browse_ok:
            return ActionResult(False, "分享链接不存在或已过期",
                                {"state": "cloud_error"})
        return ActionResult(True, "资料库 «多课程资料库»: 2 个目录,"
                                  " 匹配到课程 1 个",
                            dict(BROWSE_DETAIL))

    def cloud_mount(self, url, max_depth=2):
        """挂载替身."""
        self.mount_calls.append((url, max_depth))
        return ActionResult(True, "资料库 «多课程资料库»: 新挂载 1,"
                                  " 已挂载 0, 未匹配 1", dict(MOUNT_DETAIL))

    def set_plugin_enabled(self, plugin_id, enabled, confirm=None):
        """启用替身(confirm 回调由真实 controller 内部触发, 替身不弹框)."""
        self.confirm = confirm
        self.enabled_calls.append((plugin_id, enabled))
        return ActionResult(True, "已启用", {})


def make_page(qtbot, tmp_path, **kwargs):
    """构造被测页与替身(挂 qtbot 保活); 返回 (page, ctl, storage)."""
    storage = Storage(str(tmp_path / "cloud.db"))
    ctl = FakeController(storage, **kwargs)
    page = CloudPage(ctl)
    qtbot.addWidget(page)
    return page, ctl, storage


class TestStates:
    """两态切换."""

    def test_plugin_missing(self, qtbot, tmp_path):
        """插件未安装: 引导页."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        ctl.record = None
        page.refresh_state()
        assert page.stack.currentIndex() == PAGE_DISABLED
        storage.close()

    def test_disabled(self, qtbot, tmp_path):
        """插件未启用: 引导页."""
        page, _ctl, storage = make_page(qtbot, tmp_path, status="disabled")
        assert page.stack.currentIndex() == PAGE_DISABLED
        storage.close()

    def test_enabled_main(self, qtbot, tmp_path):
        """已启用: 主视图."""
        page, _ctl, storage = make_page(qtbot, tmp_path)
        assert page.stack.currentIndex() == PAGE_MAIN
        storage.close()


class TestActions:
    """浏览与挂载."""

    def test_enable_button(self, qtbot, tmp_path):
        """启用按钮调用 controller 并刷新状态."""
        page, ctl, storage = make_page(qtbot, tmp_path, status="disabled")
        assert page.stack.currentIndex() == PAGE_DISABLED
        page.enable_button.click()
        assert ctl.enabled_calls == [("cloud", True)]
        # 替身未真正启用, 仍在引导页(状态以 controller 为准)
        assert page.stack.currentIndex() == PAGE_DISABLED
        storage.close()

    def test_browse_fills_table(self, qtbot, tmp_path):
        """浏览: 匹配表填充, 挂载按钮可用."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        page.url_edit.setText(SHARE_URL)
        page.browse_button.click()
        assert ctl.browse_calls == [(SHARE_URL, 2)]
        tree = page.dir_tree
        assert tree.topLevelItemCount() == 2
        row0 = tree.topLevelItem(0)
        row1 = tree.topLevelItem(1)
        rows = {row0.text(0): row0, row1.text(0): row1}
        assert rows["核仪器原理"].text(1) == "核仪器原理"
        assert rows["核仪器原理"].text(2) == "待挂载"
        assert rows["其他"].text(2) == "未匹配"
        assert page.mount_button.isEnabled()
        storage.close()

    def test_browse_failure_shows_hint(self, qtbot, tmp_path):
        """浏览失败: 提示保留, 挂载按钮禁用."""
        page, _ctl, storage = make_page(qtbot, tmp_path, browse_ok=False)
        page.url_edit.setText(SHARE_URL)
        page.browse_button.click()
        assert "不存在或已过期" in page.hint_label.text()
        assert not page.mount_button.isEnabled()
        storage.close()

    def test_empty_url_hint(self, qtbot, tmp_path):
        """空链接浏览: 提示先粘贴, 不调用动作."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        page.browse_button.click()
        assert not ctl.browse_calls
        assert "请先粘贴" in page.hint_label.text()
        storage.close()

    def test_mount_invokes_action_and_signals(self, qtbot, tmp_path):
        """挂载: 调 controller, 发信号, 行状态变已挂载."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        page.url_edit.setText(SHARE_URL)
        page.browse_button.click()
        fired, messages = [], []
        page.resources_changed.connect(lambda: fired.append(True))
        page.status_message.connect(messages.append)
        page.mount_button.click()
        assert ctl.mount_calls == [(SHARE_URL, 2)]
        assert fired == [True]
        assert messages and "新挂载 1" in messages[0]
        rows = {page.dir_tree.topLevelItem(i).text(0):
                page.dir_tree.topLevelItem(i)
                for i in range(page.dir_tree.topLevelItemCount())}
        assert rows["核仪器原理"].text(2) == "已挂载"
        assert rows["其他"].text(2) == "未匹配"
        storage.close()

    def test_mount_without_browse_disabled(self, qtbot, tmp_path):
        """未浏览前挂载按钮不可用."""
        page, _ctl, storage = make_page(qtbot, tmp_path)
        assert not page.mount_button.isEnabled()
        storage.close()
