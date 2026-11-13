"""学堂缓存编排: 全量刷新差分入库, 本地课程匹配, 下载落盘登记, 待办同步.

controller(留痕动作)与插件壳共用的业务层, 仿 campus.perform_login 的
共享定位: 协议在 core/learn.py, 存取在 core/storage.py, 本模块负责
一次刷新/下载/同步事务的顺序与差分汇总.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import date

from shuimu.core.campus_net import TransportError
from shuimu.core.fileops import dedupe_path
from shuimu.core.learn import (
    LearnClient,
    LearnCourse,
    LearnError,
    SessionExpiredError,
    download_course_file_url,
    download_homework_file_url,
    parse_learn_date,
)
from shuimu.core.schedule import (
    add_todo as sched_add_todo,
    load_schedule,
    save_schedule,
)

TODO_PREFIX = "[学堂] "


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


# ---- 下载落盘 ----

def sanitize_filename(name: str) -> str:
    """服务端给的文件名不可信: 强制 basename, 去控制符与路径分隔符."""
    text = os.path.basename(str(name or "").strip())
    cleaned = "".join(ch if ch.isprintable() and ch not in "/\\:" else "_"
                      for ch in text)
    return cleaned.strip() or "learn_file"


def download_learn_file(storage, client: LearnClient, kind: str,
                        remote_id: str, wlkcid: str, out_dir: str,
                        name: str, overwrite: bool = False) -> dict:
    """下载一个学堂文件并落盘登记(引用式), 返回结果 dict.

    kind: file(课件, remote_id=wjid) | homework(作业附件, remote_id=zyid,
    自动抓第一个附件) | notice(公告附件, remote_id=ggid, 自动抓附件).
    已下载且文件还在时跳过(overwrite 强制重下); 关联了本地课程时
    登记进 resources.
    """
    course_row = None
    for row in storage.learn_list_courses():
        if row["wlkcid"] == wlkcid:
            course_row = row
            break
    kcm = course_row["kcm"] if course_row is not None else ""

    if kind == "file":
        url = download_course_file_url(remote_id)
    elif kind == "homework":
        attachments = client.homework_attachments(wlkcid, remote_id)
        if not attachments:
            raise LearnError("该作业没有可下载的附件")
        url = download_homework_file_url(wlkcid, attachments[0])
    elif kind == "notice":
        attachments = client.notice_attachments(wlkcid, remote_id)
        if not attachments:
            raise LearnError("该公告没有附件")
        url = download_course_file_url(attachments[0])
    else:
        raise LearnError(f"未知下载类别: {kind}")

    record = storage.learn_find_download(kind, remote_id)
    if (record is not None and not overwrite
            and os.path.exists(record["path"])):
        return {"path": record["path"], "resource_id": record["resource_id"],
                "skipped": True}

    data = client.download_bytes(url)
    directory = os.path.join(out_dir, sanitize_filename(kcm))
    os.makedirs(directory, exist_ok=True)
    dst = os.path.join(directory, sanitize_filename(name))
    if not overwrite:
        dst = dedupe_path(dst)
    tmp = dst + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)

    resource_id = None
    if course_row is not None and course_row["course_id"] is not None:
        resource_id = storage.add_resource(
            course_row["course_id"], "file", dst)
    storage.learn_record_download(kind, remote_id, wlkcid, dst,
                                  resource_id)
    return {"path": dst, "resource_id": resource_id, "skipped": False}


# ---- 待办同步 ----

def _todo_due(jzsj: str):
    """作业截止时间串 -> date 或 None."""
    raw = parse_learn_date(jzsj)
    return date.fromisoformat(raw) if raw else None


def sync_learn_todos(storage, schedule_path: str) -> dict:
    """未交作业同步进日程待办; 返回 {"added","updated","completed"}.

    来源集合 = learn_homework.todo_tid 映射到的待办(标题须仍带 [学堂]
    前缀, 防止手删后 tid 被复用误认领), 手动待办永不修改. 已交/已批
    作业的待办自动勾完成(不删除, 保留痕迹); 用户手动勾完成的未交作业
    待办尊重不动.
    """
    data = load_schedule(schedule_path)
    by_tid = {todo.tid: todo for todo in data.todos}
    kcm = {row["wlkcid"]: row["kcm"]
           for row in storage.learn_list_courses()}
    added = updated = completed = 0
    dirty = False
    for hw in storage.learn_list_homework():
        title = f"{TODO_PREFIX}{hw['bt']}"
        due = _todo_due(hw["jzsj"])
        course = kcm.get(hw["wlkcid"], "")
        todo = by_tid.get(hw["todo_tid"]) if hw["todo_tid"] else None
        if todo is not None and not todo.title.startswith(TODO_PREFIX):
            todo = None    # tid 已被非学堂待办占用, 视为映射失效
        if hw["state"] == "pending":
            if todo is None:
                item = sched_add_todo(data, title, due, course)
                storage.learn_set_homework_todo(hw["wlkcid"], hw["zyid"],
                                                item.tid)
                by_tid[item.tid] = item
                added += 1
                dirty = True
            elif not todo.done and (todo.title != title
                                    or todo.due != due
                                    or todo.course != course):
                todo.title, todo.due, todo.course = title, due, course
                updated += 1
                dirty = True
        elif todo is not None and not todo.done:
            todo.done = True
            completed += 1
            dirty = True
    if dirty:
        save_schedule(schedule_path, data)
    return {"added": added, "updated": updated, "completed": completed}
