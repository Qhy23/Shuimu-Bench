"""learn_sync.py 单元测试: 课程匹配与全量刷新编排(假 transport 离线)."""

import pytest

from shuimu.core.campus_net import MemoryTransport
from shuimu.core.learn import (
    HOMEWORK_PATHS,
    LearnCourse,
)
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
