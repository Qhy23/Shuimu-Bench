"""业务动作层: 登记资源, 删除课程, 整理目录等动作的统一实现.

不依赖 Qt. GUI 槽函数与 CLI 子命令都是本层的薄封装;
未来 QML 桥接层(controller QObject)同样只调这里.
每个动作完成存储变更 + 操作留痕, 返回结构化结果.
"""

import os
from dataclasses import dataclass, field

from .organize import build_plan, execute_plan
from .storage import Storage


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
    """业务动作集合, 持有 Storage. 无界面状态."""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.storage.ensure_default_course()

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
        self.storage.ensure_default_course()
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

    def preview_organize(self, directory: str) -> ActionResult:
        """生成整理计划(不落盘). detail["plan"] 为 MoveItem 列表.

        自动排除本程序数据库文件(含 -wal/-shm), 避免整理把库文件挪走.
        """
        try:
            plan = build_plan(directory)
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
