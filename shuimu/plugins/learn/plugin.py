"""网络学堂插件入口: 复用 core/learn 协议层与 learn_sync 编排, 按 action 分派.

主通道是 controller.learn_* 动作(GUI 学堂页签与 CLI learn-* 子命令,
浏览与选择体验更好); 本入口保持与通用插件框架的对接(plugin-run learn).
"""

import time

from shuimu.core.campus import SessionStore
from shuimu.core.campus_net import CookieJar, TransportError, UrllibTransport
from shuimu.core.learn import LearnClient, LearnError, SessionExpiredError
from shuimu.core.learn_sync import (
    download_learn_file,
    refresh_learn_cache,
    sync_learn_todos,
)
from shuimu.core.paths import (
    default_campus_fingerprint_path,
    default_campus_session_path,
    default_db_path,
    default_learn_download_dir,
    default_schedule_path,
)
from shuimu.core.pluginapi import (
    PluginJob,
    PluginReport,
    finding,
    make_report,
)
from shuimu.core.storage import Storage

RELOGIN_HINT = "运行 campus-login(或 GUI 校园页签)重新登录后重试"


def _session_store():
    """校园会话存储(测试可替换)."""
    return SessionStore(default_campus_session_path(),
                        default_campus_fingerprint_path())


def _transport_factory():
    """生产传输层(测试可替换为 MemoryTransport)."""
    return UrllibTransport()


def _storage() -> Storage:
    """工作台数据库(测试可替换为 tmp 库)."""
    return Storage(default_db_path())


def run(job: PluginJob) -> PluginReport:
    """按 params.action 分派: status(只读) / refresh / download / todos."""
    action = job.params.get("action", "status")
    if action == "refresh":
        return _refresh()
    if action == "download":
        return _download(job.params)
    if action == "todos":
        return _todos()
    return _status()


def _status() -> PluginReport:
    """只读缓存状态(不联网)."""
    storage = _storage()
    try:
        courses = storage.learn_list_courses()
        homework = storage.learn_list_homework()
        pending = storage.learn_list_homework(state="pending")
        linked = sum(1 for row in courses if row["course_id"] is not None)
        summary = (f"学堂缓存: {len(courses)} 课 {len(homework)} 作业"
                   f"(未交 {len(pending)})")
        findings = [
            finding("ok", "cache", summary,
                    f"已关联本地课程 {linked}/{len(courses)}"),
            finding("ok", "session",
                    "已登录" if _session_store().load() is not None
                    else "未登录(刷新前需校园登录)"),
        ]
        return make_report(findings, summary=summary)
    finally:
        storage.close()


def _save_session(store, session, jar) -> None:
    """动作成功后回写 cookie 与时间戳."""
    session["cookies"] = jar.as_dict()
    session["saved_at"] = time.time()
    store.save(session)


def _client_context():
    """载入校园会话 -> (store, session, jar, client); 未登录返回 None."""
    store = _session_store()
    session = store.load()
    if session is None:
        return None
    jar = CookieJar()
    jar.load(session.get("cookies") or {})
    return store, session, jar, LearnClient(_transport_factory(), jar)


def _refresh() -> PluginReport:
    """全量刷新缓存; 会话失效/网络异常转 error finding."""
    storage = _storage()
    try:
        context = _client_context()
        if context is None:
            return make_report(
                [finding("error", "session", "未登录校园账号", RELOGIN_HINT)],
                summary="未登录校园账号")
        store, session, jar, client = context
        try:
            outcome = refresh_learn_cache(storage, client)
        except SessionExpiredError as exc:
            return make_report(
                [finding("error", "session", str(exc), RELOGIN_HINT)],
                summary=str(exc))
        except TransportError as exc:
            return make_report(
                [finding("error", "network", f"网络异常: {exc}")],
                summary="暂时无法刷新")
        _save_session(store, session, jar)
        findings = []
        for kcm, bt in outcome.homework_new:
            findings.append(finding("ok", "homework", f"新作业: {bt}",
                                    kcm))
        for kcm, bt, old, new in outcome.homework_changed:
            findings.append(finding("warn", "homework",
                                    f"作业状态变化: {bt} ({old} -> {new})",
                                    kcm))
        for err in outcome.errors:
            findings.append(finding("warn", "course",
                                    f"课程刷新失败: {err.kcm}", err.message))
        return make_report(findings, summary=outcome.summary())
    finally:
        storage.close()


def _todos() -> PluginReport:
    """未交作业同步进日程待办."""
    storage = _storage()
    try:
        counts = sync_learn_todos(storage, default_schedule_path())
        summary = (f"待办同步: 新增 {counts['added']}, "
                   f"更新 {counts['updated']}, 自动完成 {counts['completed']}")
        findings = [finding("ok", "todos", summary)]
        return make_report(findings, summary=summary)
    finally:
        storage.close()


def _download(params: dict) -> PluginReport:
    """下载课件/附件并落盘登记."""
    kind = str(params.get("kind", "file"))
    remote_id = str(params.get("file_id", "")).strip()
    wlkcid = str(params.get("wlkcid", "")).strip()
    if not remote_id:
        return make_report(
            [finding("error", "input", "缺 file_id 参数")],
            summary="参数不全")
    if kind != "file" and not wlkcid:
        return make_report(
            [finding("error", "input", f"kind={kind} 需要 wlkcid 参数")],
            summary="参数不全")
    storage = _storage()
    try:
        if kind == "file" and not wlkcid:
            row = storage.learn_find_file(remote_id)
            if row is None:
                return make_report(
                    [finding("error", "input",
                             f"缓存中找不到课件: {remote_id}",
                             "先运行 refresh")], summary="找不到课件")
            wlkcid = row["wlkcid"]
            name = row["bt"]
        else:
            rows = (storage.learn_list_homework(wlkcid=wlkcid)
                    if kind == "homework"
                    else storage.learn_list_notices(wlkcid))
            key = "zyid" if kind == "homework" else "ggid"
            name = next((row["bt"] for row in rows
                         if row[key] == remote_id), remote_id)
        context = _client_context()
        if context is None:
            return make_report(
                [finding("error", "session", "未登录校园账号", RELOGIN_HINT)],
                summary="未登录校园账号")
        _store, session, jar, client = context
        out_dir = str(params.get("out_dir", "")).strip() \
            or default_learn_download_dir()
        try:
            result = download_learn_file(
                storage, client, kind, remote_id, wlkcid, out_dir, name,
                overwrite=bool(params.get("overwrite")))
        except SessionExpiredError as exc:
            return make_report(
                [finding("error", "session", str(exc), RELOGIN_HINT)],
                summary=str(exc))
        except (TransportError, LearnError) as exc:
            return make_report(
                [finding("error", "download", f"下载失败: {exc}")],
                summary=f"下载失败: {exc}")
        _save_session(_store, session, jar)
        verb = "已下载过" if result["skipped"] else "已下载"
        detail = result["path"]
        if result.get("resource_id") is not None:
            detail += f"(登记为资源 #{result['resource_id']})"
        return make_report(
            [finding("ok", "download", f"{verb}: {name}", detail)],
            summary=f"{verb}: {name}")
    finally:
        storage.close()
