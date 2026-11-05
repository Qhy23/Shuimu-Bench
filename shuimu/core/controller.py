"""业务动作层: 登记资源, 删除课程, 整理目录, 日程等动作的统一实现.

不依赖 Qt. GUI 槽函数与 CLI 子命令都是本层的薄封装;
未来 QML 桥接层(controller QObject)同样只调这里.
每个动作完成存储变更 + 操作留痕, 返回结构化结果.
"""

import os
from dataclasses import dataclass, field

from shuimu.core.config import (
    load_config,
    organize_rules,
    semester_start_date,
    semester_week_count,
)
from shuimu.core import fileops, trash
from shuimu.core.organize import build_plan, execute_plan
from shuimu.core.paths import (
    default_links_path,
    default_schedule_path,
    template_links_path,
    template_schedule_path,
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


@dataclass
class ActionResult:
    """动作结果: ok 是否成功, message 给用户看的摘要, detail 留痕详情."""

    ok: bool
    message: str
    detail: dict = field(default_factory=dict)


KIND_LABEL = {"file": "文件", "dir": "目录", "link": "链接"}


def detect_kind(target: str) -> str:
    """按形态判断资源类型: http(s) 为链接, 目录为 dir, 其余为 file."""
    if target.startswith(("http://", "https://")):
        return "link"
    return "dir" if os.path.isdir(target) else "file"


class Controller:
    """业务动作集合, 持有 Storage 与配置. 无界面状态."""

    def __init__(self, storage: Storage, config: dict = None,
                 schedule_path: str = None, links_path: str = None):
        self.storage = storage
        self.config = config or load_config()
        self.organize_rules = organize_rules(self.config)
        self.schedule_path = schedule_path or default_schedule_path()
        self.links_path = links_path or default_links_path()
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

    def add_resource(self, course_id: int, target: str) -> ActionResult:
        """登记文件/目录/链接进课程."""
        kind = detect_kind(target)
        if kind != "link" and not os.path.exists(target):
            return ActionResult(False, f"路径不存在: {target}")
        rid = self.storage.add_resource(course_id, kind, target)
        self.storage.log_operation("add_resource", rid, path_snapshot=target)
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

    def file_delete(self, paths: list) -> ActionResult:
        """把一批文件/目录移入回收站.

        留痕 detail 记录 原路径 -> 回收站路径 映射, 为将来恢复留数据.
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
        # Windows Shell 后端不回传回收站路径, 不可撤销, 不入栈
        self._push_undo("file_delete", [(item["src"], item["trash"])
                                        for item in items if item["trash"]])
        self.storage.log_operation(
            "file_delete", status="failed" if errors else "ok",
            path_snapshot=snapshot, detail={"items": items, "errors": errors})
        message = f"已移入回收站 {len(items)} 项"
        if errors:
            return ActionResult(False, f"{message}, 失败 {len(errors)} 项",
                                {"items": items, "errors": errors})
        return ActionResult(True, message, {"items": items})

    def file_rename(self, path: str, new_name: str) -> ActionResult:
        """同目录重命名."""
        try:
            dst = fileops.rename_entry(path, new_name)
        except (ValueError, OSError) as exc:
            return ActionResult(False, str(exc))
        self.storage.log_operation(
            "file_rename", params={"new_name": new_name},
            path_snapshot=path, detail={"dst": dst})
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
        原位置被占用或目标已缺失时该项失败, 不静默改名.
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
                elif kind in ("file_move", "file_delete"):
                    fileops.move_back(current, original)
                else:  # file_rename
                    fileops.rename_entry(current, os.path.basename(original))
                done += 1
            except (ValueError, OSError) as exc:
                errors.append(str(exc))
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

    def _push_undo(self, kind: str, items: list) -> None:
        """成功条目入撤销栈(空批不入, 上限 100 条, 超出丢最旧)."""
        if not items:
            return
        self._undo_stack.append({"kind": kind, "items": items})
        del self._undo_stack[:-100]

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
