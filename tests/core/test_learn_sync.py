"""learn_sync.py 单元测试: 课程匹配/全量刷新/待办同步(假 transport 离线)."""

import yaml

import pytest

from shuimu.core.campus_net import MemoryTransport
from shuimu.core.learn import (
    HOMEWORK_PATHS,
    LearnCourse,
    LearnHomework,
)
from shuimu.core.learn_sync import (
    TODO_PREFIX,
    sync_learn_todos,
)
from shuimu.core.schedule import load_schedule
from shuimu.core.learn_sync import (
    LearnRefreshOutcome,
    match_local_course,
    refresh_learn_cache,
)
from shuimu.core.storage import Storage
from tests.core.test_learn import (
    SEMESTER,
    WLKCID,
    _post_url,
    add_courses_route,
    add_csrf_route,
    add_files_route,
    add_homework_routes,
    add_notice_routes,
    add_semester_route,
    make_client,
)

WLKCID2 = "2025-2026-1-00000002"
COURSE_A = {"wlkcid": WLKCID, "kcm": "AI 编程", "kch": "00240431",
            "kxh": "90", "jsm": "韩老师"}
COURSE_B = {"wlkcid": WLKCID2, "kcm": "核仪器概论", "kch": "30320302",
            "kxh": "0", "jsm": "肖老师"}


def add_refresh_prelude(transport, courses=(COURSE_A, COURSE_B)):
    """刷新公共前奏: 令牌 + 学期 + 课程列表."""
    add_csrf_route(transport)
    add_semester_route(transport)
    add_courses_route(transport, list(courses))


def add_course_detail_routes(transport, wlkcid, *, homework=(), files=(),
                             notices=(), categories=()):
    """一门课的作业/课件/公告路由(URL 不含课程, 按注册顺序消费)."""
    add_homework_routes(transport, pending=list(homework))
    add_files_route(transport, wlkcid, rows=list(files),
                    categories=list(categories))
    add_notice_routes(transport, active=list(notices))


@pytest.fixture()
def storage(tmp_path):
    """隔离的存储."""
    store = Storage(str(tmp_path / "learn.db"))
    yield store
    store.close()


class TestMatchLocalCourse:
    """本地课程三级匹配."""

    def test_exact_name(self):
        """课名全等优先."""
        course = LearnCourse(wlkcid="W1", kcm="AI 编程", kch="111")
        assert match_local_course(course, {5: "AI 编程"}) == 5

    def test_prefix_either_side(self):
        """前缀互含(本地短名或学堂全名)."""
        course = LearnCourse(wlkcid="W1", kcm="AI 编程: 方法与实践")
        assert match_local_course(course, {6: "AI 编程"}) == 6
        course = LearnCourse(wlkcid="W1", kcm="AI 编")
        assert match_local_course(course, {7: "AI 编程: 方法与实践"}) == 7

    def test_course_number_in_local_name(self):
        """本地课名含学堂课号."""
        course = LearnCourse(wlkcid="W1", kcm="无关名", kch="00240431")
        assert match_local_course(course, {8: "00240431-编程课"}) == 8

    def test_no_match(self):
        """都不命中返回 None; 空名不参与匹配."""
        course = LearnCourse(wlkcid="W1", kcm="量子力学", kch="999")
        assert match_local_course(course, {5: "AI 编程"}) is None
        assert match_local_course(
            LearnCourse(wlkcid="W2", kcm=""), {5: "AI 编程"}) is None


class TestRefreshLearnCache:
    """全量刷新编排."""

    def test_first_refresh_counts_and_link(self, storage):
        """首刷: 计数正确, 本地同名课程自动关联, 元信息落库."""
        local_id = storage.add_course("AI 编程")
        transport = MemoryTransport()
        add_refresh_prelude(transport)
        add_course_detail_routes(
            transport, WLKCID,
            homework=[{"zyid": "Z1", "bt": "作业一",
                       "jzsj": "1794758340000"}],
            files=[{"kjxxid": "K1", "wjid": "WJ1", "bt": "讲义一",
                    "wjdx": 10, "kjflid": "C1"}],
            notices=[{"ggid": "G1", "bt": "公告一", "ggnr": ""}],
            categories=[("C1", "电子教案")])
        add_course_detail_routes(transport, WLKCID2)   # 课 B 全空
        progress = []
        outcome = refresh_learn_cache(
            storage, make_client(transport),
            on_progress=lambda *args: progress.append(args))
        assert outcome.semester == SEMESTER
        assert outcome.courses_new == 2
        assert outcome.homework_new == [("AI 编程", "作业一")]
        assert outcome.files_new == [("AI 编程", "讲义一")]
        assert outcome.notices_new == [("AI 编程", "公告一")]
        assert not outcome.errors
        links = {row["kcm"]: row["course_id"]
                 for row in storage.learn_list_courses()}
        assert links["AI 编程"] == local_id        # 自动关联
        assert links["核仪器概论"] is None          # 无匹配不强建
        assert storage.learn_meta_get("semester_id") == SEMESTER
        assert storage.learn_meta_get("refreshed_at") is not None
        assert [call[0] for call in progress].count("course") == 3  # 2 课 + 收尾

    def test_second_refresh_detects_state_change(self, storage):
        """二刷: 同一作业 pending -> submitted 记入 changed."""
        transport = MemoryTransport()
        add_refresh_prelude(transport)
        add_course_detail_routes(
            transport, WLKCID, homework=[{"zyid": "Z1", "bt": "作业一"}])
        add_course_detail_routes(transport, WLKCID2)
        refresh_learn_cache(storage, make_client(transport))

        transport2 = MemoryTransport()
        add_refresh_prelude(transport2)
        add_homework_routes(
            transport2,
            submitted=[{"zyid": "Z1", "bt": "作业一", "scsj": "2026"}])
        add_files_route(transport2, WLKCID, rows=[])
        add_notice_routes(transport2)
        add_course_detail_routes(transport2, WLKCID2)    # 课 B 全空
        outcome = refresh_learn_cache(storage, make_client(transport2))
        assert outcome.homework_changed == [("AI 编程", "作业一",
                                             "pending", "submitted")]
        assert not outcome.homework_new

    def test_single_course_failure_not_fatal(self, storage):
        """单课协议失败记 errors, 不拖垮整轮."""
        transport = MemoryTransport()
        add_refresh_prelude(transport)
        add_course_detail_routes(
            transport, WLKCID, homework=[{"zyid": "Z1", "bt": "作业一"}])
        # 课 B 的首个作业请求返回非 JSON -> ProtocolChangedError
        transport.add("POST", _post_url(HOMEWORK_PATHS["pending"]),
                      text="<html>not json</html>")
        outcome = refresh_learn_cache(storage, make_client(transport))
        assert outcome.homework_new == [("AI 编程", "作业一")]
        assert len(outcome.errors) == 1
        assert outcome.errors[0].wlkcid == WLKCID2
        assert "JSON" in outcome.errors[0].message


class TestOutcomeSummary:
    """汇总文案."""

    def test_summary_with_errors(self):
        """汇总含错误计数."""
        outcome = LearnRefreshOutcome(
            semester="2026-2027-1", courses_new=2, homework_new=[("a", "b")],
            homework_changed=[("a", "b", "pending", "submitted")],
            notices_new=[], files_new=[("c", "d")],
            errors=[object(), object()])
        text = outcome.summary()
        assert "2026-2027-1" in text and "新课程 2" in text
        assert "新作业 1" in text and "状态变化 1" in text
        assert "失败课程 2" in text


def write_schedule(path, todos):
    """写最小日程文件(todos 段)."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"todos": todos}, f, allow_unicode=True)


def read_todos(path):
    """读回待办列表."""
    return load_schedule(path).todos


class TestSyncTodos:
    """未交作业同步进日程待办."""

    def _seed(self, storage, state="pending", jzsj="1794758340000"):
        """一门课 + 一份作业(Z1)."""
        storage.learn_sync_courses(
            SEMESTER, [LearnCourse(**COURSE_A)])
        storage.learn_sync_homework(
            [LearnHomework(wlkcid=WLKCID, zyid="Z1", state=state,
                           bt="作业一", jzsj=jzsj)])

    def test_pending_adds_and_rewrites_mapping(self, storage, tmp_path):
        """未交作业新增待办(前缀/截止/课名), 映射回写, 二次幂等."""
        self._seed(storage)
        path = str(tmp_path / "s.yaml")
        write_schedule(path, [])
        counts = sync_learn_todos(storage, path)
        assert counts == {"added": 1, "updated": 0, "completed": 0}
        todos = read_todos(path)
        assert len(todos) == 1
        assert todos[0].title == f"{TODO_PREFIX}作业一"
        assert todos[0].course == "AI 编程"
        assert todos[0].due is not None
        row = storage.learn_list_homework(wlkcid=WLKCID)[0]
        assert row["todo_tid"] == todos[0].tid
        again = sync_learn_todos(storage, path)
        assert again == {"added": 0, "updated": 0, "completed": 0}

    def test_manual_todos_untouched(self, storage, tmp_path):
        """手动待办(无映射)永不被修改."""
        self._seed(storage)
        path = str(tmp_path / "s.yaml")
        write_schedule(path, [{"id": 1, "title": "我的手动待办",
                               "due": "2026-01-01", "done": False}])
        sync_learn_todos(storage, path)
        todos = read_todos(path)
        manual = [t for t in todos if t.title == "我的手动待办"]
        assert len(manual) == 1 and manual[0].tid == 1
        assert not manual[0].done and manual[0].due is not None

    def test_submitted_auto_completes(self, storage, tmp_path):
        """作业变已交: 待办自动勾完成; 再跑不重复计."""
        self._seed(storage)
        path = str(tmp_path / "s.yaml")
        write_schedule(path, [])
        sync_learn_todos(storage, path)
        # 模拟重新拉取: 同一作业变为已交
        storage.learn_sync_homework(
            [LearnHomework(wlkcid=WLKCID, zyid="Z1", state="submitted",
                           bt="作业一", scsj="2026")])
        counts = sync_learn_todos(storage, path)
        assert counts["completed"] == 1
        todos = read_todos(path)
        assert todos[0].done
        again = sync_learn_todos(storage, path)
        assert again["completed"] == 0

    def test_user_done_pending_kept(self, storage, tmp_path):
        """用户手动勾完成的未交作业待办不被翻回."""
        self._seed(storage)
        path = str(tmp_path / "s.yaml")
        write_schedule(path, [])
        sync_learn_todos(storage, path)
        todos = read_todos(path)
        edited = [{"id": t.tid, "title": t.title, "done": True}
                  for t in todos]
        write_schedule(path, edited)
        counts = sync_learn_todos(storage, path)
        assert counts == {"added": 0, "updated": 0, "completed": 0}
        assert read_todos(path)[0].done

    def test_deleted_todo_recreated(self, storage, tmp_path):
        """映射待办被手删且 tid 复用给手动待办: 重建且不误碰."""
        self._seed(storage)
        path = str(tmp_path / "s.yaml")
        write_schedule(path, [])
        sync_learn_todos(storage, path)
        row = storage.learn_list_homework(wlkcid=WLKCID)[0]
        # 用户手删学堂待办, 该 tid 被新的手动待办占据
        write_schedule(path, [{"id": row["todo_tid"],
                               "title": "新手动待办", "done": False}])
        counts = sync_learn_todos(storage, path)
        assert counts["added"] == 1
        titles = {t.title: t.done for t in read_todos(path)}
        assert titles["新手动待办"] is False        # 未被误碰
        assert any(t.startswith("[学堂]") for t in titles)

    def test_due_change_updates(self, storage, tmp_path):
        """截止时间变化: 原地更新待办."""
        self._seed(storage)
        path = str(tmp_path / "s.yaml")
        write_schedule(path, [])
        sync_learn_todos(storage, path)
        old_due = read_todos(path)[0].due
        storage.learn_sync_homework(
            [LearnHomework(wlkcid=WLKCID, zyid="Z1", state="pending",
                           bt="作业一改名", jzsj="1797177540000")])
        counts = sync_learn_todos(storage, path)
        assert counts["updated"] == 1 and counts["added"] == 0
        todo = read_todos(path)[0]
        assert "作业一改名" in todo.title
        assert todo.due != old_due or todo.due is not None
