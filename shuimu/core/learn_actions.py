"""学堂业务动作: Controller 的混入部分(门禁/刷新/下载/详情/关联).

依赖宿主属性与方法: storage, plugins, config, _campus_store(),
_campus_transport_factory. 动作语义与留痕约定同 controller 本体.
"""

import time

from shuimu.core.campus_net import CookieJar, TransportError
from shuimu.core.actions import ActionResult
from shuimu.core.learn import LearnClient, LearnError, SessionExpiredError
from shuimu.core.learn_sync import download_learn_file, refresh_learn_cache
from shuimu.core.paths import default_learn_download_dir


class LearnActions:
    """学堂动作集合(由 Controller 继承)."""

    def _learn_gate(self):
        """学堂插件门禁: 未启用/不可用时返回失败结果(零联网), 可用为 None."""
        record = self.plugins.get("learn")
        if record is None:
            return ActionResult(False, "学堂插件未安装")
        if record.status == "broken":
            return ActionResult(False, f"学堂插件损坏: {record.error}")
        if record.status == "disabled":
            return ActionResult(False, "学堂插件未启用(需网络授权)")
        return None

    def _learn_context(self):
        """载入校园会话与 jar.

        返回 (context, failure): 成功时 context = (store, session, jar,
        client) 且 failure 为 None; 未登录时 context 为 None, failure
        为可直接返回的失败结果.
        """
        store = self._campus_store()
        session = store.load()
        if session is None:
            return None, ActionResult(False, "未登录校园账号",
                                      {"state": "logged_out"})
        jar = CookieJar()
        jar.load(session.get("cookies") or {})
        client = LearnClient(self._campus_transport_factory(), jar)
        return (store, session, jar, client), None

    def _learn_save_session(self, store, session, jar) -> None:
        """动作成功后回写 cookie(learn 请求可能刷新 webvpn 会话)."""
        session["cookies"] = jar.as_dict()
        session["saved_at"] = time.time()
        store.save(session)

    def learn_status(self) -> ActionResult:
        """只读: 插件态 + 会话态 + 缓存计数 + 上次刷新(零联网不留痕)."""
        record = self.plugins.get("learn")
        semester = self.storage.learn_meta_get("semester_id")
        refreshed_raw = self.storage.learn_meta_get("refreshed_at")
        refreshed_at = float(refreshed_raw) if refreshed_raw else None
        courses = self.storage.learn_list_courses()
        homework = self.storage.learn_list_homework()
        detail = {
            "plugin": None if record is None else record.status,
            "semester": semester,
            "refreshed_at": refreshed_at,
            "courses": len(courses),
            "homework": len(homework),
            "linked": sum(1 for row in courses
                          if row["course_id"] is not None),
        }
        stamp = ""
        if refreshed_at is not None:
            stamp = time.strftime("%m-%d %H:%M",
                                  time.localtime(refreshed_at))
            stamp = f", 上次刷新 {stamp}"
        message = (f"学堂缓存: {len(courses)} 课 {len(homework)} 作业"
                   f"{stamp}")
        detail["campus"] = self._campus_store().load() is not None
        return ActionResult(True, message, detail)

    def learn_refresh(self, on_progress=None) -> ActionResult:
        """全量刷新学堂缓存并留痕; 会话失效区分三态."""
        gate = self._learn_gate()
        if gate is not None:
            return gate
        context, failure = self._learn_context()
        if failure is not None:
            return failure
        store, session, jar, client = context
        try:
            outcome = refresh_learn_cache(self.storage, client,
                                          on_progress=on_progress)
            self._learn_save_session(store, session, jar)
        except SessionExpiredError as exc:
            return ActionResult(False, str(exc), {"state": "expired"})
        except TransportError as exc:
            return ActionResult(False, f"暂时无法刷新(网络异常): {exc}",
                                {"state": "network_error"})
        detail = {"semester": outcome.semester,
                  "courses_new": outcome.courses_new,
                  "homework_new": len(outcome.homework_new),
                  "homework_changed": len(outcome.homework_changed),
                  "notices_new": len(outcome.notices_new),
                  "files_new": len(outcome.files_new),
                  "errors": [f"{err.kcm}: {err.message}"
                             for err in outcome.errors]}
        self.storage.log_operation(
            "learn_refresh", status="ok" if not outcome.errors else "failed",
            detail=detail)
        message = outcome.summary()
        return ActionResult(True, message, detail)

    def learn_homework_detail(self, wlkcid: str, zyid: str) -> ActionResult:
        """作业描述: 优先读缓存(零联网), 缺失时拉取并缓存. 不留痕."""
        for row in self.storage.learn_list_homework(wlkcid=wlkcid):
            if row["zyid"] == zyid and row["detail_html"]:
                return ActionResult(True, "已缓存", {"html": row["detail_html"],
                                                      "cached": True})
        gate = self._learn_gate()
        if gate is not None:
            return gate
        context, failure = self._learn_context()
        if failure is not None:
            return failure
        store, session, jar, client = context
        try:
            markup = client.homework_detail(zyid)
            self._learn_save_session(store, session, jar)
        except SessionExpiredError as exc:
            return ActionResult(False, str(exc), {"state": "expired"})
        except (TransportError, LearnError) as exc:
            return ActionResult(False, f"暂时无法获取: {exc}",
                                {"state": "network_error"})
        self.storage.learn_save_homework_detail(wlkcid, zyid, markup)
        return ActionResult(True, "已获取", {"html": markup, "cached": False})

    def learn_download(self, kind: str, remote_id: str, wlkcid: str = None,
                       out_dir: str = None, name: str = None,
                       overwrite: bool = False) -> ActionResult:
        """下载课件/附件, 落盘并引用式登记进关联课程, 留痕."""
        gate = self._learn_gate()
        if gate is not None:
            return gate
        if kind not in ("file", "homework", "notice"):
            return ActionResult(False, f"未知下载类别: {kind}")
        if kind != "file" and not wlkcid:
            return ActionResult(False, "该类别需要提供课程 id")
        wlkcid = wlkcid or self._learn_file_course(remote_id)
        if wlkcid is None:
            return ActionResult(False, f"缓存中找不到文件: {remote_id}")
        if not name:
            name = self._learn_display_name(kind, remote_id, wlkcid)
        out_dir = out_dir or self.config.get("learn", {}).get(
            "download_dir", "") or default_learn_download_dir()
        context, failure = self._learn_context()
        if failure is not None:
            return failure
        store, session, jar, client = context
        try:
            result = download_learn_file(
                self.storage, client, kind, remote_id, wlkcid, out_dir,
                name, overwrite=overwrite)
            self._learn_save_session(store, session, jar)
        except SessionExpiredError as exc:
            return ActionResult(False, str(exc), {"state": "expired"})
        except (TransportError, LearnError) as exc:
            return ActionResult(False, f"暂时无法下载: {exc}",
                                {"state": "network_error"})
        path = result["path"]
        message = ("已下载过: " if result["skipped"] else "已下载: ") + path
        self.storage.log_operation(
            "learn_download", resource_id=result.get("resource_id"),
            path_snapshot=path,
            params={"kind": kind, "remote_id": remote_id},
            detail={"skipped": result["skipped"],
                    "resource_id": result.get("resource_id")})
        return ActionResult(True, message,
                            {"path": path, "skipped": result["skipped"],
                             "resource_id": result.get("resource_id")})

    def _learn_file_course(self, wjid: str):
        """课件 wjid -> wlkcid(经缓存查找)."""
        row = self.storage.learn_find_file(wjid)
        return row["wlkcid"] if row is not None else None

    def _learn_display_name(self, kind: str, remote_id: str,
                            wlkcid: str) -> str:
        """按缓存行给下载文件取名."""
        if kind == "file":
            row = self.storage.learn_find_file(remote_id)
            return row["bt"] if row is not None else remote_id
        rows = self.storage.learn_list_homework(wlkcid=wlkcid) \
            if kind == "homework" else \
            self.storage.learn_list_notices(wlkcid)
        for row in rows:
            if (row["zyid"] if kind == "homework" else row["ggid"]) \
                    == remote_id:
                return row["bt"]
        return remote_id

    def learn_set_course_link(self, wlkcid: str, course_id) -> ActionResult:
        """手动设置/清除学堂课程与本地课程的关联, 留痕."""
        if not any(row["wlkcid"] == wlkcid
                   for row in self.storage.learn_list_courses()):
            return ActionResult(False, f"学堂课程不存在: {wlkcid}")
        if course_id is not None and \
                self.storage.get_course(course_id) is None:
            return ActionResult(False, f"本地课程不存在: #{course_id}")
        self.storage.learn_set_course_link(wlkcid, course_id)
        self.storage.log_operation(
            "learn_link_course",
            params={"wlkcid": wlkcid, "course_id": course_id})
        message = "已取消关联" if course_id is None else \
            f"已关联课程 #{course_id}"
        return ActionResult(True, message,
                            {"wlkcid": wlkcid, "course_id": course_id})
