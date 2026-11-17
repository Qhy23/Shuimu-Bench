"""业务动作层: 登记资源, 删除课程, 整理目录, 日程等动作的统一实现.

不依赖 Qt. GUI 槽函数与 CLI 子命令都是本层的薄封装;
未来 QML 桥接层(controller QObject)同样只调这里.
每个动作完成存储变更 + 操作留痕, 返回结构化结果.
"""

import os
import time

from shuimu.core.config import (
    load_config,
    organize_rules,
    semester_start_date,
    semester_week_count,
)
from shuimu.core import fileops, trash
from shuimu.core.organize import build_plan, execute_plan
from shuimu.core.campus import (
    CampusClient,
    SessionStore,
    perform_login,
)
from shuimu.core.campus_net import (
    CookieJar,
    TransportError,
    UrllibTransport,
)
from shuimu.core.folder_actions import FolderActions
from shuimu.core.learn_actions import LearnActions
from shuimu.core.cloud_actions import CloudActions
from shuimu.core.ai_actions import AiActions
from shuimu.core.ai import UrllibAiTransport
from shuimu.core.paths import (
    default_ai_config_path,
    default_campus_fingerprint_path,
    default_campus_session_path,
    default_links_path,
    default_schedule_path,
    template_links_path,
    template_schedule_path,
)
from shuimu.core.pluginapi import PluginJob, PluginReport
from shuimu.core.pluginman import (
    PluginError,
    PluginManager,
    validate_params,
)
from shuimu.core.quicklinks import (
    QuickLink,
    ensure_links_file,
    load_quick_links,
    save_quick_links,
)
from shuimu.core.schedule import (
    Event,
    ScheduleData,
    add_todo as sched_add_todo,
    ensure_schedule_file,
    load_schedule,
    next_series_id,
    save_schedule,
    toggle_todo as sched_toggle_todo,
)
from shuimu.core.schedule_import import parse_calendar_html
from shuimu.core.storage import Storage


from shuimu.core.actions import ActionResult, KIND_LABEL  # noqa: F401


def detect_kind(target: str) -> str:
    """按形态判断资源类型: http(s) 为链接, 目录为 dir, 其余为 file."""
    if target.startswith(("http://", "https://")):
        return "link"
    return "dir" if os.path.isdir(target) else "file"


class Controller(FolderActions, LearnActions, CloudActions, AiActions):
    """业务动作集合, 持有 Storage 与配置. 无界面状态."""

    def __init__(self, storage: Storage, config: dict = None,
                 schedule_path: str = None, links_path: str = None,
                 plugins_state_path: str = None,
                 external_plugins_dir: str = None,
                 campus_session_path: str = None,
                 campus_transport_factory=None,
                 ai_transport_factory=None,
                 ai_config_path: str = None):
        self.storage = storage
        self.config = config or load_config()
        self.organize_rules = organize_rules(self.config)
        self.schedule_path = schedule_path or default_schedule_path()
        self.links_path = links_path or default_links_path()
        self._plugins_state_path = plugins_state_path
        self._external_plugins_dir = external_plugins_dir
        self._campus_session_path = campus_session_path
        self._campus_transport_factory = campus_transport_factory \
            or UrllibTransport
        self._ai_transport_factory = ai_transport_factory or UrllibAiTransport
        self._ai_config_path = ai_config_path or default_ai_config_path()
        self._plugin_manager = None
        self._semester_start = semester_start_date(self.config)
        self._week_count = semester_week_count(self.config)
        self._schedule_seeded = False
        self._links_seeded = False
        self._undo_stack = []   # 文件页操作撤销栈(内存, LIFO, 不跨会话)
        self.storage.ensure_default_course(self.config["course"]["default_name"])

    def add_course(self, name: str) -> ActionResult:
        """新建课程."""
        name = name.strip()
        if not name:
            return ActionResult(False, "课程名不能为空")
        cid = self.storage.add_course(name)
        return ActionResult(True, f"已建课程 #{cid} {name}", {"course_id": cid})

    def delete_course(self, course_id: int) -> ActionResult:
        """删除课程, 级联移除其资源索引(磁盘文件不动)."""
        course = self.storage.get_course(course_id)
        if course is None:
            return ActionResult(False, f"课程不存在: #{course_id}")
        removed = self.storage.delete_course(course_id)
        self.storage.log_operation(
            "delete_course", path_snapshot=course["name"],
            detail={"removed_resources": removed})
        self.storage.ensure_default_course(self.config["course"]["default_name"])
        return ActionResult(
            True, f"已删除课程 {course['name']}(级联移除 {removed} 条索引)",
            {"removed_resources": removed})

    def add_resource(self, course_id: int, target: str,
                     folder_id: int = None) -> ActionResult:
        """登记文件/目录/链接进课程(同课程重复登记幂等拒绝).

        folder_id 为归属虚拟文件夹(None=课程根), 须经校验属于该课程.
        """
        kind = detect_kind(target)
        if kind != "link" and not os.path.exists(target):
            return ActionResult(False, f"路径不存在: {target}")
        if folder_id is not None:
            folder = self.storage.get_folder(folder_id)
            if folder is None or folder["course_id"] != course_id:
                return ActionResult(False, f"目标文件夹不可用: #{folder_id}")
        existing = self.storage.find_resource(course_id, target)
        if existing is not None:
            return ActionResult(
                False, f"该课程已登记: {target}",
                {"resource_id": existing["id"], "kind": kind, "duplicate": True})
        rid = self.storage.add_resource(course_id, kind, target,
                                        folder_id=folder_id)
        self.storage.log_operation("add_resource", rid, path_snapshot=target,
                                   params={"folder_id": folder_id})
        return ActionResult(True, f"已登记 #{rid} [{KIND_LABEL[kind]}] {target}",
                            {"resource_id": rid, "kind": kind})

    def remove_resource(self, resource_id: int) -> ActionResult:
        """从索引移除资源(不动磁盘文件)."""
        res = self.storage.get_resource(resource_id)
        if res is None:
            return ActionResult(False, f"资源不存在: #{resource_id}")
        self.storage.remove_resource(resource_id)
        self.storage.log_operation("remove_resource",
                                   path_snapshot=res["path_or_url"])
        return ActionResult(True, f"已移除 #{resource_id}")

    def search_resources(self, keyword: str) -> ActionResult:
        """跨课程搜索资源(只读, 不留痕). detail["rows"] 为匹配行, 含 course_name."""
        keyword = keyword.strip()
        if not keyword:
            return ActionResult(False, "关键词不能为空")
        rows = self.storage.search_resources(keyword)
        return ActionResult(True, f"找到 {len(rows)} 条匹配", {"rows": rows})

    def preview_organize(self, directory: str) -> ActionResult:
        """生成整理计划(不落盘). detail["plan"] 为 MoveItem 列表.

        自动排除本程序数据库文件(含 -wal/-shm), 避免整理把库文件挪走.
        归类规则来自配置(organize 段).
        """
        try:
            plan = build_plan(directory, rules=self.organize_rules)
        except NotADirectoryError:
            return ActionResult(False, f"目录不存在: {directory}")
        db = self.storage.db_path
        plan = [i for i in plan
                if i.src != db and not i.src.startswith(db + "-")]
        return ActionResult(True, f"预览: {len(plan)} 个文件待移动",
                            {"plan": plan})

    def execute_organize(self, directory: str, plan: list) -> ActionResult:
        """执行整理并留痕."""
        moved, errors = execute_plan(plan)
        status = "ok" if not errors else "failed"
        self.storage.log_operation(
            "organize", status=status, params={"directory": directory},
            path_snapshot=directory, detail={"moved": moved, "errors": errors})
        if errors:
            return ActionResult(False, f"移动 {moved} 个, 失败 {len(errors)} 个",
                                {"moved": moved, "errors": errors})
        return ActionResult(True, f"整理完成: 移动 {moved} 个",
                            {"moved": moved})

    def cancel_organize(self, directory: str) -> None:
        """记录一次取消的整理."""
        self.storage.log_operation("organize", status="cancelled",
                                   path_snapshot=directory)

    # ---- 文件操作 ----

    def file_copy(self, sources: list, dst_dir: str) -> ActionResult:
        """把一批文件/目录复制到 dst_dir, 冲突自动改名, 留痕."""
        return self._transfer(fileops.copy_batch, "file_copy", "复制",
                              sources, dst_dir)

    def file_move(self, sources: list, dst_dir: str) -> ActionResult:
        """把一批文件/目录移动到 dst_dir(剪切粘贴), 冲突自动改名, 留痕."""
        return self._transfer(fileops.move_batch, "file_move", "移动",
                              sources, dst_dir)

    def _retarget_index(self, old: str, new: str) -> int:
        """文件路径变更后同步登记行(old -> new, 含子孙), 返回更新行数."""
        rows = self.storage.resources_under(old)
        for row in rows:
            suffix = row["path_or_url"][len(old):]
            self.storage.retarget_resource(row["id"], new + suffix)
        return len(rows)

    def _drop_index(self, paths: list) -> list:
        """从索引移除 paths 及其子孙的登记行, 返回完整行 dict(供撤销恢复)."""
        removed: dict = {}
        for path in paths:
            for row in self.storage.resources_under(path):
                removed[row["id"]] = dict(row)
        for rid in removed:
            self.storage.remove_resource(rid)
        return list(removed.values())

    def file_delete(self, paths: list) -> ActionResult:
        """把一批文件/目录移入回收站, 同步移除对应索引行.

        留痕 detail 记录 原路径 -> 回收站路径 映射与索引同步行数,
        被移除的索引行完整存入撤销条目供恢复.
        """
        paths = [p for p in paths if p]
        if not paths:
            return ActionResult(False, "没有可操作的项目")
        items, errors = [], []
        for path in paths:
            try:
                result = trash.move_to_trash(path, self.config)
                items.append({"src": path, "backend": result.backend,
                              "trash": result.trash_path})
            except OSError as exc:
                errors.append(f"{path}: {exc}")
        snapshot = paths[0] if len(paths) == 1 else f"{paths[0]} 等 {len(paths)} 项"
        removed_rows = self._drop_index([item["src"] for item in items])
        # Windows Shell 后端不回传回收站路径, 不可撤销, 不入栈
        self._push_undo("file_delete",
                        [(item["src"], item["trash"])
                         for item in items if item["trash"]],
                        removed_rows=removed_rows)
        self.storage.log_operation(
            "file_delete", status="failed" if errors else "ok",
            path_snapshot=snapshot,
            detail={"items": items, "errors": errors,
                    "index_removed": len(removed_rows)})
        message = f"已移入回收站 {len(items)} 项"
        if errors:
            return ActionResult(False, f"{message}, 失败 {len(errors)} 项",
                                {"items": items, "errors": errors})
        return ActionResult(True, message, {"items": items})

    def file_rename(self, path: str, new_name: str) -> ActionResult:
        """同目录重命名, 同步索引路径(含登记的子孙)."""
        try:
            dst = fileops.rename_entry(path, new_name)
        except (ValueError, OSError) as exc:
            return ActionResult(False, str(exc))
        index_n = self._retarget_index(path, dst)
        self.storage.log_operation(
            "file_rename", params={"new_name": new_name},
            path_snapshot=path,
            detail={"dst": dst, "index_updated": index_n})
        self._push_undo("file_rename", [(path, dst)])
        message = (f"已重命名 {os.path.basename(path)}"
                   f" -> {os.path.basename(dst)}")
        return ActionResult(True, message, {"dst": dst})

    def file_mkdir(self, parent: str, name: str = None) -> ActionResult:
        """在 parent 下新建文件夹(重名自动改名), 消息含实际创建名."""
        try:
            created = fileops.make_dir(parent, name)
        except (ValueError, OSError) as exc:
            return ActionResult(False, str(exc))
        self.storage.log_operation(
            "file_mkdir", params={"parent": parent},
            path_snapshot=created, detail={"name": os.path.basename(created)})
        self._push_undo("file_mkdir", [(None, created)])
        return ActionResult(True, f"已创建文件夹 {os.path.basename(created)}",
                            {"path": created})

    def _transfer(self, batch, action: str, verb: str,
                  sources: list, dst_dir: str) -> ActionResult:
        """复制/移动共用骨架: 校验 -> 批操作 -> 留痕."""
        sources = [s for s in sources if s]
        if not sources:
            return ActionResult(False, "没有可操作的项目")
        if not os.path.isdir(dst_dir):
            return ActionResult(False, f"目标目录不存在: {dst_dir}")
        items = batch(sources, dst_dir)
        detail = {
            "items": [{"src": i.src, "dst": i.dst, "status": i.status}
                      for i in items],
            "renamed": sum(
                1 for i in items
                if i.status == "ok"
                and i.dst != os.path.join(dst_dir, os.path.basename(i.src))),
        }
        ok_n = sum(1 for i in items if i.status == "ok")
        skip_n = sum(1 for i in items if i.status == "skipped")
        fail_n = sum(1 for i in items if i.status == "failed")
        index_n = 0
        if action == "file_move":   # 移动改路径, 复制不动原索引
            for i in items:
                if i.status == "ok":
                    index_n += self._retarget_index(i.src, i.dst)
            detail["index_updated"] = index_n
        self._push_undo(action, [(i.src, i.dst) for i in items
                                 if i.status == "ok"])
        self.storage.log_operation(
            action, status="failed" if fail_n else "ok",
            params={"dst_dir": dst_dir, "count": len(sources)},
            path_snapshot=dst_dir, detail=detail)
        message = f"已{verb} {ok_n} 项到 {dst_dir}"
        if skip_n:
            message += f", 跳过 {skip_n} 项"
        if fail_n:
            message += f", 失败 {fail_n} 项"
        if detail["renamed"]:
            message += f"({detail['renamed']} 项重名自动改名)"
        return ActionResult(not fail_n, message, detail)

    def undo(self) -> ActionResult:
        """撤销最近一次文件页操作(内存栈, LIFO, 不跨会话, 无 redo).

        - 复制/新建 -> 目标项移入回收站
        - 移动      -> 移回原位
        - 删除      -> 从回收站恢复到原路径
        - 重命名    -> 改回旧名
        索引随文件一并回滚: 复制/新建撤销时移除其后登记的行,
        移动/重命名撤销时登记路径改回, 删除撤销时重插原登记行
        (原位置被占用或目标已缺失时该项失败, 不静默改名).
        """
        if not self._undo_stack:
            return ActionResult(False, "没有可撤销的操作")
        entry = self._undo_stack.pop()
        kind, items = entry["kind"], entry["items"]
        done, errors = 0, []
        snapshot = None
        for original, current in items:
            snapshot = snapshot or current
            try:
                if kind in ("file_copy", "file_mkdir"):
                    trash.move_to_trash(current, self.config)
                    self._drop_index([current])
                elif kind in ("file_move", "file_delete"):
                    fileops.move_back(current, original)
                    if kind == "file_move":
                        self._retarget_index(current, original)
                else:  # file_rename
                    fileops.rename_entry(current, os.path.basename(original))
                    self._retarget_index(current, original)
                done += 1
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
        if kind == "file_delete":
            # 恢复成功的文件重插原登记行(路径已存在者; 含 find 幂等防护)
            for row in entry.get("removed_rows", []):
                path = row["path_or_url"]
                if not os.path.exists(path) or \
                        self.storage.find_resource(row["course_id"], path):
                    continue
                self.storage.add_resource(row["course_id"], row["kind"], path)
        self.storage.log_operation(
            "undo", status="failed" if errors else "ok",
            params={"undid": kind}, path_snapshot=snapshot,
            detail={"done": done, "errors": errors})
        label = {"file_copy": "复制", "file_move": "移动", "file_delete": "删除",
                 "file_rename": "重命名", "file_mkdir": "新建文件夹"}[kind]
        message = f"已撤销{label} {done} 项"
        if errors:
            return ActionResult(False, f"{message}, 失败 {len(errors)} 项",
                                {"done": done, "errors": errors})
        return ActionResult(True, message, {"done": done})

    def _push_undo(self, kind: str, items: list, **extra) -> None:
        """成功条目入撤销栈(空批不入, 上限 100 条, 超出丢最旧)."""
        if not items:
            return
        entry = {"kind": kind, "items": items}
        entry.update(extra)
        self._undo_stack.append(entry)
        del self._undo_stack[:-100]

    # ---- 插件 ----

    @property
    def plugins(self) -> PluginManager:
        """插件管理器(懒构造一次后缓存; 首次访问触发目录扫描)."""
        if self._plugin_manager is None:
            self._plugin_manager = PluginManager(
                external_dir=self._external_plugins_dir,
                state_path=self._plugins_state_path)
        return self._plugin_manager

    def run_plugin(self, plugin_id: str, targets: list,
                   params: dict = None, confirm=None) -> ActionResult:
        """运行插件并统一留痕(action 为 plugin:<id>), 不入撤销栈.

        流程: 记录/状态检查 -> applies_to 匹配 -> 参数校验 ->
        write 权限确认 -> 调入口 run -> 留痕 -> ActionResult.
        产出经 report.outputs 归档引用, 撤销语义由插件自定义(当前无).

        留痕 status 与 ActionResult.ok 分离: status 记"运行是否成功"
        (ok/failed/cancelled), ok 记"检查是否通过"(报告含 error 级
        finding 即 False) -- status="ok" 而 ok=False 是合法组合.
        """
        record = self.plugins.get(plugin_id)
        if record is None:
            return ActionResult(False, f"插件不存在: {plugin_id}")
        manifest = record.manifest
        if record.status == "broken":
            return ActionResult(False, f"插件损坏: {record.error}")
        if record.status == "disabled":
            return ActionResult(False, f"插件已禁用: {manifest.name}")
        targets = [t for t in targets if t]
        if not targets and "app" not in manifest.applies_to:
            return ActionResult(False, "没有可操作的目标")
        kinds = {detect_kind(t) for t in targets}
        if kinds and not kinds <= set(manifest.applies_to):
            return ActionResult(
                False, f"目标类型不适用: 插件仅适用于 {'/'.join(manifest.applies_to)}")
        clean, errors = validate_params(manifest, params)
        if errors:
            return ActionResult(False, "参数错误: " + "; ".join(errors))
        snapshot = self._plugin_snapshot(targets) if targets else manifest.name
        secret_names = {p.name for p in manifest.params
                        if p.type == "secret"}
        log_params = {"targets": targets}
        log_params.update({key: value for key, value in clean.items()
                           if key not in secret_names})
        secrets_given = sorted(secret_names & clean.keys())
        if secrets_given:
            log_params["secret_params"] = secrets_given   # 只记名不记值
        if "write" in manifest.permissions and not (
                confirm and confirm(manifest)):
            self.storage.log_operation(
                f"plugin:{plugin_id}", status="cancelled",
                params=log_params, path_snapshot=snapshot)
            return ActionResult(False, "已取消: 写权限未确认")
        job = PluginJob(targets=targets, params=clean)
        try:
            report = self.plugins.entry(plugin_id)(job)
            if not isinstance(report, PluginReport):
                raise PluginError("入口返回值不是 PluginReport")
        # 插件代码任意异常都不能崩宿主, 此边界必须宽捕获
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.storage.log_operation(
                f"plugin:{plugin_id}", status="failed",
                params=log_params, path_snapshot=snapshot,
                detail={"error": str(exc)})
            return ActionResult(False, f"插件运行失败: {exc}")
        op_id = self.storage.log_operation(
            f"plugin:{plugin_id}", params=log_params,
            path_snapshot=snapshot, detail=self._plugin_detail(report))
        return ActionResult(report.ok, f"{manifest.name}: {report.summary}",
                            {"report": report, "op_id": op_id})

    def cancel_plugin(self, plugin_id: str, targets: list) -> None:
        """记录一次未运行即取消的插件调用(对齐 cancel_organize)."""
        targets = [t for t in targets if t]
        self.storage.log_operation(
            f"plugin:{plugin_id}", status="cancelled",
            params={"targets": targets},
            path_snapshot=self._plugin_snapshot(targets) if targets else None)

    def set_plugin_enabled(self, plugin_id: str, enabled: bool,
                           confirm=None) -> ActionResult:
        """启用/禁用插件: 管理器状态切换 + plugins.yaml 写回 + 留痕.

        network 插件启用须经 confirm(manifest) 确认(对齐 write 先例),
        拒绝则留痕 cancelled.
        """
        record = self.plugins.get(plugin_id)
        if record is None:
            return ActionResult(False, f"插件不存在: {plugin_id}")
        if record.status == "broken":
            return ActionResult(False,
                                f"插件损坏, 无法切换: {record.error}")
        if enabled and "network" in record.manifest.permissions \
                and not (confirm and confirm(record.manifest)):
            self.storage.log_operation(
                "plugin_enable", status="cancelled",
                params={"plugin": plugin_id},
                detail={"reason": "network 权限未确认"})
            return ActionResult(False, "已取消: 网络权限未确认")
        try:
            self.plugins.set_enabled(plugin_id, enabled)
        except PluginError as exc:
            return ActionResult(False, str(exc))
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        action = "plugin_enable" if enabled else "plugin_disable"
        self.storage.log_operation(action, params={"plugin": plugin_id})
        verb = "启用" if enabled else "禁用"
        return ActionResult(True, f"已{verb}插件 {record.manifest.name}")

    @staticmethod
    def _plugin_snapshot(targets: list) -> str:
        """插件目标留痕快照(写法对齐 file_delete)."""
        return targets[0] if len(targets) == 1 \
            else f"{targets[0]} 等 {len(targets)} 项"

    @staticmethod
    def _plugin_detail(report: PluginReport) -> dict:
        """报告转留痕 detail: findings 前 100 条, 超出附 _truncated."""
        detail = {"summary": report.summary,
                  "findings": [{"severity": f.severity, "check": f.check,
                                "title": f.title, "detail": f.detail}
                               for f in report.findings[:100]],
                  "outputs": list(report.outputs),
                  "counts": {sev: sum(1 for f in report.findings
                                      if f.severity == sev)
                             for sev in ("error", "warn", "ok")}}
        if len(report.findings) > 100:
            detail["_truncated"] = True
        return detail

    # ---- 校园 ----

    def _campus_gate(self):
        """校园插件门禁: 未启用/不可用时返回失败结果(不发网络), 可用为 None."""
        record = self.plugins.get("campus")
        if record is None:
            return ActionResult(False, "校园插件未安装")
        if record.status == "broken":
            return ActionResult(False, f"校园插件损坏: {record.error}")
        if record.status == "disabled":
            return ActionResult(False, "校园插件未启用(需网络授权)")
        return None

    def _campus_store(self) -> SessionStore:
        """校园会话存储(会话路径可注入; 指纹路径经 paths 分发)."""
        return SessionStore(
            self._campus_session_path or default_campus_session_path(),
            default_campus_fingerprint_path())

    def campus_login(self, username: str, password: str,
                     ask_2fa=None) -> ActionResult:
        """登录校园统一认证并留痕(密码/验证码绝不入留痕与结果 detail).

        ask_2fa 交互语义见 core.campus.perform_login.
        """
        gate = self._campus_gate()
        if gate is not None:
            return gate
        username = str(username).strip()
        if not username:
            return ActionResult(False, "学号不能为空")
        outcome = perform_login(self._campus_store(),
                                self._campus_transport_factory,
                                username, password, ask_2fa=ask_2fa)
        status = "ok" if outcome.state == "success" else (
            "cancelled" if outcome.state == "cancelled" else "failed")
        detail = {"state": outcome.state}
        if outcome.profile is not None:
            detail["name"] = outcome.profile.name
            detail["username"] = outcome.profile.username
        self.storage.log_operation(
            "campus_login", status=status,
            params={"username": username}, detail=detail)
        return ActionResult(outcome.state == "success", outcome.message,
                            {"state": outcome.state,
                             "profile": outcome.profile,
                             "options": outcome.options})

    def campus_status(self, check: bool = False) -> ActionResult:
        """读会话状态; check=True 联网校验并刷新本人信息(只读不留痕).

        网络异常与失效区分: 网络异常保留会话仅提示; 失效提示重新登录.
        """
        store = self._campus_store()
        session = store.load()
        if session is None:
            return ActionResult(False, "未登录", {"state": "logged_out"})
        if not check:
            cached = session.get("profile") or {}
            message = f"已登录 {session.get('username', '')} " \
                      f"{cached.get('name', '')}".strip()
            return ActionResult(True, message, {"state": "cached",
                                                "session": session})
        gate = self._campus_gate()
        if gate is not None:
            return gate
        jar = CookieJar()
        jar.load(session.get("cookies") or {})
        client = CampusClient(self._campus_transport_factory(), jar,
                              store.fingerprint())
        try:
            profile = client.check_session(session.get("username", ""))
        except TransportError as exc:
            return ActionResult(False, f"暂时无法校验(网络异常): {exc}",
                                {"state": "network_error"})
        if profile is None:
            return ActionResult(False, "会话已失效, 请重新登录",
                                {"state": "expired"})
        session["profile"] = {"username": profile.username,
                              "name": profile.name, "raw": profile.raw}
        session["checked_at"] = time.time()
        store.save(session)
        return ActionResult(True, f"会话有效: {profile.name}",
                            {"state": "valid", "profile": profile})

    def campus_logout(self) -> ActionResult:
        """清除本机会话(设备指纹保留), 留痕."""
        store = self._campus_store()
        existed = store.load() is not None
        store.clear_session()
        self.storage.log_operation("campus_logout", status="ok",
                                   detail={"had_session": existed})
        message = "已登出, 会话已清除" if existed else "本无会话"
        return ActionResult(True, message, {"had_session": existed})

    # ---- 日程 ----

    def semester_start(self):
        """学期起点(建议第 1 周周一), 未配置返回 None."""
        return self._semester_start

    def semester_week_count(self) -> int:
        """学期总周数(课程+考试), 未配置返回 0(不限制)."""
        return self._week_count

    def schedule_snapshot(self) -> ScheduleData:
        """当前日程快照. 首次调用或数据文件缺失时(重新)播种."""
        if not self._schedule_seeded or not os.path.isfile(self.schedule_path):
            ensure_schedule_file(self.schedule_path, template_schedule_path())
            self._schedule_seeded = True
        return load_schedule(self.schedule_path)

    def add_todo(self, title: str, due=None, course: str = "") -> ActionResult:
        """新建任务备忘, 写回 YAML 并留痕."""
        title = title.strip()
        if not title:
            return ActionResult(False, "标题不能为空")
        data = self.schedule_snapshot()
        todo = sched_add_todo(data, title, due, course)
        try:
            save_schedule(self.schedule_path, data)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "add_todo", params={"title": title},
            detail={"tid": todo.tid, "due": str(todo.due) if todo.due else None})
        return ActionResult(True, f"已添加待办 #{todo.tid} {title}",
                            {"tid": todo.tid})

    def toggle_todo(self, tid: int) -> ActionResult:
        """按 id 翻转待办完成状态, 写回 YAML 并留痕."""
        data = self.schedule_snapshot()
        todo = sched_toggle_todo(data, tid)
        if todo is None:
            return ActionResult(
                False, f"待办不存在: #{tid}(文件可能已被外部修改)")
        try:
            save_schedule(self.schedule_path, data)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "toggle_todo", params={"tid": tid}, detail={"done": todo.done})
        return ActionResult(
            True, f"待办 #{tid} 已标记为{'完成' if todo.done else '未完成'}",
            {"tid": tid, "done": todo.done})

    def add_manual(self, events: list) -> ActionResult:
        """手动添加一批日程(单次或按周数展开的系列), 追加进 manual 段.

        多条事件共享一个新系列号 sid, 供整组删除/修改.
        """
        if not events:
            return ActionResult(False, "没有可添加的日程")
        data = self.schedule_snapshot()
        sid = next_series_id(data) if len(events) > 1 else 0
        for event in events:
            event.sid = sid
        data.manual.extend(events)
        try:
            save_schedule(self.schedule_path, data)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "add_manual", params={"title": events[0].title},
            detail={"count": len(events), "sid": sid})
        scope = f"{len(events)} 条(同系列 #{sid})" if sid else "1 条"
        return ActionResult(True, f"已添加手动日程 {scope}", {"count": len(events)})

    def update_event(self, section: str, old, new,
                     whole_series: bool = False) -> ActionResult:
        """修改一条日程(section 为 courses/manual, 按值定位第一条相等项).

        whole_series 且 old 有系列号时, 标题/地点/起止时间应用到该系列全部
        场次(各场次日期保持不变).
        """
        data = self.schedule_snapshot()
        entries = getattr(data, section, None)
        if entries is None:
            return ActionResult(False, f"未知日程段: {section}")
        if whole_series and old.sid:
            updated = 0
            for i, event in enumerate(entries):
                if event.sid == old.sid:
                    entries[i] = Event(event.date, new.start, new.end,
                                       new.title, new.location, event.sid)
                    updated += 1
            if not updated:
                return ActionResult(False, f"系列不存在: #{old.sid}")
        else:
            try:
                index = entries.index(old)
            except ValueError:
                return ActionResult(False, "日程不存在(文件可能已被外部修改)")
            entries[index] = Event(new.date, new.start, new.end, new.title,
                                   new.location, old.sid)
            updated = 1
        try:
            save_schedule(self.schedule_path, data)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "update_event",
            params={"section": section, "title": new.title},
            detail={"updated": updated, "series": bool(whole_series and old.sid)})
        scope = f"系列 {updated} 场" if updated > 1 else "1 条"
        return ActionResult(True, f"已修改{scope}日程", {"updated": updated})

    def remove_event(self, section: str, event,
                     whole_series: bool = False) -> ActionResult:
        """删除一条日程; whole_series 且有系列号时删除整个系列."""
        data = self.schedule_snapshot()
        entries = getattr(data, section, None)
        if entries is None:
            return ActionResult(False, f"未知日程段: {section}")
        if whole_series and event.sid:
            remaining = [e for e in entries if e.sid != event.sid]
            removed = len(entries) - len(remaining)
            if not removed:
                return ActionResult(False, f"系列不存在: #{event.sid}")
            updated_list = remaining
        else:
            try:
                entries.remove(event)
            except ValueError:
                return ActionResult(False, "日程不存在(文件可能已被外部修改)")
            removed = 1
            updated_list = entries
        setattr(data, section, updated_list)
        try:
            save_schedule(self.schedule_path, data)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "remove_event", params={"section": section, "title": event.title},
            detail={"removed": removed, "series": bool(whole_series and event.sid)})
        scope = f"系列 {removed} 场" if removed > 1 else "1 条"
        return ActionResult(True, f"已删除{scope}日程", {"removed": removed})

    def import_courses(self, path: str) -> ActionResult:
        """从个人校历 html 导入课程(联排课自动合并), 替换 courses 段.

        manual 与 todos 不动.
        """
        try:
            parsed = parse_calendar_html(path)
        except (OSError, ValueError) as exc:
            return ActionResult(False, f"导入失败: {exc}")
        data = self.schedule_snapshot()
        replaced = len(data.courses)
        data.courses = parsed.events
        try:
            save_schedule(self.schedule_path, data)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "import_courses", params={"path": path},
            detail={"courses": len(parsed.events), "replaced": replaced,
                    "merged": parsed.merged})
        message = (f"已导入课程 {len(parsed.events)} 条"
                   f"(联排合并 {parsed.merged} 处, 替换原 {replaced} 条)")
        if parsed.warnings:
            message += "; " + "; ".join(parsed.warnings)
        return ActionResult(True, message,
                            {"courses": len(parsed.events),
                             "replaced": replaced,
                             "merged": parsed.merged,
                             "warnings": parsed.warnings})

    def course_names(self) -> list:
        """课程名列表, 供新建待办对话框下拉."""
        return [c["name"] for c in self.storage.list_courses()]

    # ---- 常用链接 ----

    def _links_snapshot(self) -> list:
        """当前常用链接列表. 首次调用或数据文件缺失时(重新)播种."""
        if not self._links_seeded or not os.path.isfile(self.links_path):
            ensure_links_file(self.links_path, template_links_path())
            self._links_seeded = True
        return load_quick_links(self.links_path)

    def list_quick_links(self) -> list:
        """常用链接列表(只读, 不留痕)."""
        return self._links_snapshot()

    def add_quick_link(self, name: str, url: str,
                       group: str = "") -> ActionResult:
        """新增常用链接(可带分组), 写回 YAML 并留痕."""
        name, url, group = name.strip(), url.strip(), group.strip()
        if not name:
            return ActionResult(False, "名称不能为空")
        if not url.startswith(("http://", "https://")):
            return ActionResult(False, "URL 须以 http:// 或 https:// 开头")
        links = self._links_snapshot()
        if any(link.name == name for link in links):
            return ActionResult(False, f"名称已存在: {name}")
        links.append(QuickLink(name, url, group))
        try:
            save_quick_links(self.links_path, links)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "add_quick_link", params={"name": name},
            detail={"url": url, "group": group})
        return ActionResult(True, f"已添加链接 {name}", {"name": name})

    def update_quick_link(self, old_name: str, name: str, url: str,
                          group: str = "") -> ActionResult:
        """按旧名称定位修改常用链接(含分组), 写回 YAML 并留痕.

        改分组为原地更新: 条目在文件中的位置不变, 显示分组由渲染层重聚.
        """
        name, url, group = name.strip(), url.strip(), group.strip()
        if not name:
            return ActionResult(False, "名称不能为空")
        if not url.startswith(("http://", "https://")):
            return ActionResult(False, "URL 须以 http:// 或 https:// 开头")
        links = self._links_snapshot()
        target = next((link for link in links if link.name == old_name), None)
        if target is None:
            return ActionResult(
                False, f"链接不存在: {old_name}(文件可能已被外部修改)")
        if name != old_name and any(link.name == name for link in links):
            return ActionResult(False, f"名称已存在: {name}")
        target.name, target.url, target.group = name, url, group
        try:
            save_quick_links(self.links_path, links)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "update_quick_link", path_snapshot=old_name,
            params={"name": name}, detail={"url": url, "group": group})
        scope = name if name == old_name else f"{old_name} -> {name}"
        return ActionResult(True, f"已更新链接 {scope}")

    def remove_quick_link(self, name: str) -> ActionResult:
        """删除常用链接, 写回 YAML 并留痕."""
        links = self._links_snapshot()
        target = next((link for link in links if link.name == name), None)
        if target is None:
            return ActionResult(
                False, f"链接不存在: {name}(文件可能已被外部修改)")
        links.remove(target)
        try:
            save_quick_links(self.links_path, links)
        except OSError as exc:
            return ActionResult(False, f"保存失败: {exc}")
        self.storage.log_operation(
            "remove_quick_link", path_snapshot=name,
            detail={"url": target.url})
        return ActionResult(True, f"已删除链接 {name}")
