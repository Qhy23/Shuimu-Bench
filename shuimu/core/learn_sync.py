"""学堂缓存编排: 全量刷新差分入库, 本地课程匹配, 汇总结果.

controller(留痕动作)与插件壳共用的业务层, 仿 campus.perform_login 的
共享定位: 协议在 core/learn.py, 存取在 core/storage.py, 本模块负责
一次刷新事务的顺序与差分汇总(下载与待办同步在后续提交加入).
"""

import time
from dataclasses import dataclass, field

from shuimu.core.campus_net import TransportError
from shuimu.core.learn import (
    LearnClient,
    LearnCourse,
    LearnError,
    SessionExpiredError,
)


@dataclass
class LearnRefreshError:
    """刷新过程中单门课程的失败记录(不拖垮整轮)."""

    wlkcid: str
    kcm: str
    message: str


@dataclass
class LearnRefreshOutcome:
    """一轮全量刷新的汇总(计数与单课错误)."""

    semester: str = ""
    courses_new: int = 0
    courses_removed: int = 0
    homework_new: list = field(default_factory=list)     # [(kcm, bt)]
    homework_changed: list = field(default_factory=list)  # [(kcm, bt, old, new)]
    notices_new: list = field(default_factory=list)       # [(kcm, bt)]
    files_new: list = field(default_factory=list)         # [(kcm, bt)]
    errors: list = field(default_factory=list)            # [LearnRefreshError]

    def summary(self) -> str:
        """单行中文汇总."""
        parts = [f"学期 {self.semester}" if self.semester else "学堂",
                 f"新课程 {self.courses_new}"]
        if self.courses_removed:
            parts.append(f"移除 {self.courses_removed}")
        parts.extend([f"新作业 {len(self.homework_new)}",
                      f"状态变化 {len(self.homework_changed)}",
                      f"新公告 {len(self.notices_new)}",
                      f"新课件 {len(self.files_new)}"])
        if self.errors:
            parts.append(f"失败课程 {len(self.errors)}")
        return ", ".join(parts)


def match_local_course(course: LearnCourse, local: dict) -> int:
    """学堂课程 -> 本地课程 id; 不匹配返回 None.

    local 为 {course_id: name}. 三级匹配: 课名全等 > 课名前缀互含 >
    本地课名含课号; 一旦某学堂课程已关联, 刷新不再自动改判(由调用方
    只对未关联行调用本函数保证).
    """
    name = (course.kcm or "").strip()
    entries = [(cid, (value or "").strip())
               for cid, value in local.items()]
    for cid, local_name in entries:
        if name and local_name and name == local_name:
            return cid
    for cid, local_name in entries:
        if not name or not local_name:
            continue
        if name.startswith(local_name) or local_name.startswith(name):
            return cid
    kch = (course.kch or "").strip()
    if kch:
        for cid, local_name in entries:
            if kch in local_name:
                return cid
    return None


def refresh_learn_cache(storage, client: LearnClient,
                        on_progress=None) -> LearnRefreshOutcome:
    """全量刷新: 令牌(必要时自动漫游) -> 学期 -> 课程差分 -> 逐课
    作业/课件/公告差分; 单课网络/协议失败记 errors 不拖垮整轮,
    会话失效直接上抛(由调用方转"需重新登录").

    on_progress(stage, index, total, kcm) 在每门课前后被调用
    (stage 为 "course"), 供 GUI 更新进度.
    """
    outcome = LearnRefreshOutcome()
    client.csrf()
    semester = client.current_semester()
    outcome.semester = semester.semester_id
    courses = client.courses(semester.semester_id)
    diff = storage.learn_sync_courses(semester.semester_id, courses)
    outcome.courses_new = len(diff["new"])
    outcome.courses_removed = len(diff["removed"])

    local = {row["id"]: row["name"] for row in storage.list_courses()}
    stored = storage.learn_list_courses()
    by_wlkcid = {course.wlkcid: course for course in courses}
    for row in stored:
        if row["course_id"] is not None:
            continue
        course = by_wlkcid.get(row["wlkcid"])
        if course is None:
            continue
        course_id = match_local_course(course, local)
        if course_id is not None:
            storage.learn_set_course_link(row["wlkcid"], course_id)

    total = len(stored)
    for index, row in enumerate(stored):
        wlkcid, kcm = row["wlkcid"], row["kcm"]
        if on_progress is not None:
            on_progress("course", index, total, kcm)
        try:
            hw_diff = storage.learn_sync_homework(
                client.homework_all(wlkcid))
            outcome.homework_new.extend(
                (kcm, item[2]) for item in hw_diff["new"])
            outcome.homework_changed.extend(
                (kcm, item[2], item[3], item[4])
                for item in hw_diff["changed"])
            categories = client.file_categories(wlkcid)
            file_diff = storage.learn_sync_files(
                client.files(wlkcid), categories)
            outcome.files_new.extend(
                (kcm, item[2]) for item in file_diff["new"])
            notice_diff = storage.learn_sync_notices(
                client.notices(wlkcid))
            outcome.notices_new.extend(
                (kcm, item[2]) for item in notice_diff["new"])
        except (TransportError, LearnError) as exc:
            if isinstance(exc, SessionExpiredError):
                raise    # 会话失效终止整轮, 由调用方转"需重新登录"
            outcome.errors.append(LearnRefreshError(wlkcid, kcm, str(exc)))
    if on_progress is not None:
        on_progress("course", total, total, "")

    storage.learn_meta_set("semester_id", semester.semester_id)
    storage.learn_meta_set("refreshed_at", str(time.time()))
    return outcome
