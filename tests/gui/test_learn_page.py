"""learn_page 测试: 三态/装载/下载动作(offscreen, 假 controller + 真临时库)."""

from types import SimpleNamespace

from shuimu.core.actions import ActionResult
from shuimu.core.learn import (
    LearnCourse,
    LearnFile,
    LearnHomework,
    LearnNotice,
)
from shuimu.core.storage import Storage
from shuimu.gui.learn_page import (
    PAGE_DISABLED,
    PAGE_MAIN,
    PAGE_LOGIN,
    LearnPage,
)
from tests.core.test_learn import SEMESTER

WLKCID = "2025-2026-1-00000001"


def make_record(status="ok"):
    """learn 插件记录替身."""
    return SimpleNamespace(status=status, error="",
                           manifest=SimpleNamespace(name="网络学堂"))


class FakeController:
    """LearnPage 依赖的 controller 替身(动作记录, 零网络)."""

    def __init__(self, storage, status="ok", logged_in=True,
                 refresh_ok=True):
        self.storage = storage
        self.record = make_record(status)
        self.logged_in = logged_in
        self.refresh_ok = refresh_ok
        self.plugins = SimpleNamespace(get=lambda _pid: self.record)
        self.todo_calls = 0
        self.refresh_calls = []
        self.download_calls = []
        self.enabled_calls = []

    def campus_status(self, _check=False):
        """会话替身."""
        if not self.logged_in:
            return ActionResult(False, "未登录", {"state": "logged_out"})
        return ActionResult(True, "已登录", {"state": "cached"})

    def learn_refresh(self, on_progress=None):
        """刷新替身: 记录回调并调用一次."""
        self.refresh_calls.append(on_progress)
        if not self.refresh_ok:
            return ActionResult(False, "校园会话已失效",
                                {"state": "expired"})
        if on_progress is not None:
            on_progress("course", 1, 1, "AI 编程")
        return ActionResult(True, "学期 X, 新作业 1", {})

    def learn_homework_detail(self, _wlkcid, zyid):
        """描述替身."""
        return ActionResult(True, "ok", {"html": f"<p>{zyid}</p>",
                                         "cached": True})

    def learn_sync_todos(self):
        """待办同步替身."""
        self.todo_calls += 1
        return ActionResult(True, "待办同步: 新增 1", {"added": 1})

    def learn_download(self, kind, remote_id, **_kwargs):
        """下载替身."""
        self.download_calls.append((kind, remote_id))
        return ActionResult(True, f"已下载: {remote_id}",
                            {"path": "/tmp/x", "skipped": False,
                             "resource_id": 5})

    def set_plugin_enabled(self, plugin_id, enabled, _confirm=None):
        """启用替身."""
        self.enabled_calls.append((plugin_id, enabled))
        return ActionResult(True, "已启用", {})


def seed(storage):
    """预置缓存: 一门课(已关联) + 三态作业 + 课件 + 公告."""
    local = storage.add_course("AI 编程")
    storage.learn_sync_courses(SEMESTER, [LearnCourse(
        wlkcid=WLKCID, kcm="AI 编程", kch="00240431")])
    storage.learn_sync_homework([
        LearnHomework(wlkcid=WLKCID, zyid="Z1", state="pending",
                      bt="作业一", jzsj="1794758340000"),
        LearnHomework(wlkcid=WLKCID, zyid="Z2", state="graded",
                      bt="作业二", cj="4.5"),
    ])
    storage.learn_sync_files([LearnFile(
        wlkcid=WLKCID, kjxxid="K1", wjid="WJ1", bt="讲义.pdf", wjdx=2048)])
    storage.learn_sync_notices([LearnNotice(
        wlkcid=WLKCID, ggid="G1", bt="公告一", body_html="<p>正文</p>",
        sfyd=False)])
    storage.learn_set_course_link(WLKCID, local)
    storage.learn_meta_set("semester_id", SEMESTER)
    storage.learn_meta_set("refreshed_at", "1000000000")


def make_page(qtbot, tmp_path, **kwargs):
    """构造被测页与替身(挂 qtbot 保活); 返回 (page, ctl, storage)."""
    storage = Storage(str(tmp_path / "gui.db"))
    ctl = FakeController(storage, **kwargs)
    page = LearnPage(ctl)
    qtbot.addWidget(page)
    return page, ctl, storage


class TestStates:
    """三态切换."""

    def test_plugin_missing(self, qtbot, tmp_path):
        """插件未安装: 引导页."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        ctl.record = None
        page.refresh_state()
        assert page.stack.currentIndex() == PAGE_DISABLED
        storage.close()

    def test_not_logged_in(self, qtbot, tmp_path):
        """未登录校园: 登录引导页."""
        page, _ctl, storage = make_page(qtbot, tmp_path,
                                        logged_in=False)
        assert page.stack.currentIndex() == PAGE_LOGIN
        storage.close()

    def test_main_with_empty_cache(self, qtbot, tmp_path):
        """已登录空缓存: 主视图显示空提示."""
        page, _ctl, storage = make_page(qtbot, tmp_path)
        assert page.stack.currentIndex() == PAGE_MAIN
        assert page.empty_label.isVisibleTo(page)
        storage.close()


class TestReload:
    """缓存装载."""

    def test_courses_homework_files_notices(self, qtbot, tmp_path):
        """课程关联标记/作业三分组/课件公告列表."""
        page, _ctl, storage = make_page(qtbot, tmp_path)
        seed(storage)
        page.reload()
        assert page.stack.currentIndex() == PAGE_MAIN
        assert page.course_list.count() == 1
        assert page.course_list.item(0).text().startswith("● AI 编程")
        # 作业树: 3 组, pending 1 个叶子, graded 1 个叶子
        top = page.homework_tree.topLevelItem(0)
        labels = [page.homework_tree.topLevelItem(i).text(0)
                  for i in range(page.homework_tree.topLevelItemCount())]
        assert labels == ["未交 (1)", "已交未批 (0)", "已批 (1)"]
        assert top.child(0).text(0) == "作业一"
        assert "截止" in top.child(0).text(1)
        # 课件与公告
        assert page.files_list.count() == 1
        assert "讲义.pdf" in page.files_list.item(0).text()
        assert page.notice_list.count() == 1
        assert "[未读]" in page.notice_list.item(0).text()
        storage.close()

    def test_notice_selection_shows_body(self, qtbot, tmp_path):
        """公告选中显示正文."""
        page, _ctl, storage = make_page(qtbot, tmp_path)
        seed(storage)
        page.reload()
        page.notice_list.setCurrentRow(0)
        assert "正文" in page.notice_browser.toPlainText()
        storage.close()

    def test_new_marker(self, qtbot, tmp_path):
        """晚于刷新阈值的条目标\"新\"."""
        page, _ctl, storage = make_page(qtbot, tmp_path)
        seed(storage)
        storage.learn_meta_set("refreshed_at", "1")   # 阈值极早
        page.reload()
        assert page.files_list.item(0).text().startswith("[新] 讲义.pdf")
        storage.close()


class TestActions:
    """刷新与下载."""

    def test_refresh_success(self, qtbot, tmp_path):
        """刷新调用进度回调并重载."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        page._refresh()  # pylint: disable=protected-access
        assert len(ctl.refresh_calls) == 1
        storage.close()

    def test_refresh_expired_switches_page(self, qtbot, tmp_path,
                                           monkeypatch):
        """刷新遇会话失效: 切登录引导并保留提示(warn 打桩防模态挂起)."""
        monkeypatch.setattr("shuimu.gui.learn_page.warn",
                            lambda *_args, **_kwargs: None)
        page, _ctl, storage = make_page(qtbot, tmp_path,
                                        refresh_ok=False)
        page._refresh()  # pylint: disable=protected-access
        assert page.stack.currentIndex() == PAGE_LOGIN
        assert "失效" in page.login_hint.text()
        storage.close()

    def test_download_selected_file(self, qtbot, tmp_path):
        """课件下载走 controller 且发出资源变更信号."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        seed(storage)
        page.reload()
        fired = []
        page.resources_changed.connect(lambda: fired.append(True))
        page.files_list.setCurrentRow(0)
        page._download_selected_file()  # pylint: disable=protected-access
        assert ctl.download_calls == [("file", "WJ1")]
        assert fired == [True]
        storage.close()

    def test_download_selected_homework(self, qtbot, tmp_path):
        """作业附件下载取选中叶子."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        seed(storage)
        page.reload()
        leaf = page.homework_tree.topLevelItem(0).child(0)
        page.homework_tree.setCurrentItem(leaf)
        page._download_homework_attachment()  # pylint: disable=protected-access
        assert ctl.download_calls == [("homework", "Z1")]
        storage.close()


class TestSyncTodosButton:
    """同步待办按钮."""

    def test_button_invokes_action(self, qtbot, tmp_path):
        """点击按钮调用 controller 并提示."""
        page, ctl, storage = make_page(qtbot, tmp_path)
        messages = []
        page.status_message.connect(messages.append)
        page.todos_button.click()
        assert ctl.todo_calls == 1
        assert messages and "待办同步" in messages[0]
        storage.close()
