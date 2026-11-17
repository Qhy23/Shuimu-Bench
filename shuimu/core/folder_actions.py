"""虚拟文件夹与目录导入动作: Controller 的混入层.

左栏索引/链接资源管理器的业务面 (2026-11-16~17): 文件夹层级仅存在于
folders 表, 与磁盘目录无关; 目录导入是把磁盘结构瞬时快照为虚拟文件夹
层级 + 文件索引, 不建目录索引. 对齐 learn_actions/cloud_actions 的
"动作沉 core, 界面归插件/主窗口"分层.
"""

import os

from shuimu.core.actions import ActionResult


class FolderActions:
    """文件夹 CRUD/移动与目录导入动作, 经 Controller 混入."""

    def import_directory(self, course_id: int, path: str,
                         folder_id: int = None) -> ActionResult:
        """把磁盘目录导入为虚拟文件夹层级 + 文件索引(不建目录索引).

        顶层建一个以目录名命名的虚拟文件夹, 子目录递归建文件夹,
        文件登记为所在层文件夹的索引条目; 同课程已登记的文件跳过.
        只取导入瞬间的磁盘结构, 之后左栏文件夹与磁盘目录不再关联.
        """
        if not os.path.isdir(path):
            return ActionResult(False, f"不是目录: {path}")
        if folder_id is not None:
            folder = self.storage.get_folder(folder_id)
            if folder is None or folder["course_id"] != course_id:
                return ActionResult(False, f"目标文件夹不可用: #{folder_id}")
        name = os.path.basename(os.path.abspath(path))
        try:
            root_fid = self.storage.add_folder(course_id, folder_id, name)
        except ValueError as exc:
            return ActionResult(False, str(exc))
        stats = self._import_children(course_id, root_fid, path)
        stats["folders"] += 1                  # 计入顶层文件夹本身
        self.storage.log_operation(
            "import_directory", params={"path": path, "folder_id": folder_id},
            path_snapshot=path, detail=dict(stats, root_folder=root_fid))
        message = (f"已导入目录 {name}: {stats['folders']} 个文件夹, "
                   f"{stats['files']} 个文件")
        if stats["skipped"]:
            message += f"(跳过已登记 {stats['skipped']})"
        return ActionResult(True, message, {"folder_id": root_fid, **stats})

    def _import_children(self, course_id: int, folder_id: int,
                         path: str) -> dict:
        """递归导入一层目录: 子目录建文件夹, 文件登记索引(重复跳过).

        单项失败(权限/损坏等)跳过不拖垮整目录.
        """
        stats = {"folders": 0, "files": 0, "skipped": 0}
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name)
        except OSError:
            return stats
        for entry in entries:
            try:
                if entry.is_dir():
                    sub = self.storage.add_folder(course_id, folder_id,
                                                  entry.name)
                    stats["folders"] += 1
                    deeper = self._import_children(course_id, sub, entry.path)
                    for key in stats:
                        stats[key] += deeper[key]
                elif entry.is_file() \
                        and self.storage.find_resource(
                            course_id, entry.path) is None:
                    self.storage.add_resource(course_id, "file", entry.path,
                                              folder_id=folder_id)
                    stats["files"] += 1
                elif entry.is_file():
                    stats["skipped"] += 1
            except (ValueError, OSError):
                continue
        return stats

    def folder_create(self, course_id: int, parent_id,
                      name: str) -> ActionResult:
        """新建虚拟文件夹(parent_id None=课程根), 同名兄弟拒绝."""
        name = name.strip()
        if not name:
            return ActionResult(False, "名称不能为空")
        if self.storage.get_course(course_id) is None:
            return ActionResult(False, f"课程不存在: #{course_id}")
        try:
            fid = self.storage.add_folder(course_id, parent_id, name)
        except ValueError as exc:
            return ActionResult(False, str(exc))
        self.storage.log_operation("folder_create",
                                   params={"name": name,
                                           "parent_id": parent_id},
                                   path_snapshot=name)
        return ActionResult(True, f"已新建文件夹 {name}",
                            {"folder_id": fid})

    def folder_rename(self, folder_id: int, name: str) -> ActionResult:
        """文件夹改名(仅索引层, 不动磁盘)."""
        folder = self.storage.get_folder(folder_id)
        if folder is None:
            return ActionResult(False, f"文件夹不存在: #{folder_id}")
        name = name.strip()
        if not name:
            return ActionResult(False, "名称不能为空")
        if name == folder["name"]:
            return ActionResult(False, "名称未变化")
        siblings = self.storage.list_folders(folder["course_id"])
        if any(f["name"] == name and f["parent_id"] == folder["parent_id"]
               and f["id"] != folder_id for f in siblings):
            return ActionResult(False, f"同级已有同名文件夹: {name}")
        self.storage.folder_rename(folder_id, name)
        self.storage.log_operation("folder_rename",
                                   params={"name": name},
                                   path_snapshot=folder["name"])
        return ActionResult(True,
                            f"已重命名文件夹 {folder['name']} -> {name}")

    def folder_delete(self, folder_id: int,
                      cascade: bool = False) -> ActionResult:
        """删除虚拟文件夹; cascade=True 连同子孙文件夹与其中索引一并移除.

        两种模式都只动索引, 磁盘文件不受影响.
        """
        folder = self.storage.get_folder(folder_id)
        if folder is None:
            return ActionResult(False, f"文件夹不存在: #{folder_id}")
        children = self.storage.count_folder_children(folder_id)
        if children and not cascade:
            return ActionResult(
                False, f"文件夹非空(含 {children} 项), 请先移走条目"
                       "或选择\"全部移除并删除\"")
        descendants = self.storage.folder_descendants(folder_id)
        removed = 0
        if cascade:
            removed = self.storage.remove_resources_in_folders(descendants)
        self.storage.folder_delete_tree(folder_id)
        self.storage.log_operation(
            "folder_delete", path_snapshot=folder["name"],
            detail={"cascade": cascade, "removed_resources": removed})
        message = f"已删除文件夹 {folder['name']}"
        if removed:
            message += f"(级联移除 {removed} 条索引)"
        return ActionResult(True, message, {"removed_resources": removed})

    def folder_move(self, folder_id: int, parent_id) -> ActionResult:
        """移动文件夹到另一文件夹下(None=课程根), 含环检测."""
        folder = self.storage.get_folder(folder_id)
        if folder is None:
            return ActionResult(False, f"文件夹不存在: #{folder_id}")
        if parent_id == folder["parent_id"]:
            return ActionResult(False, "已在目标位置")
        if parent_id == folder_id:
            return ActionResult(False, "不能移入自身")
        if parent_id is not None:
            parent = self.storage.get_folder(parent_id)
            if parent is None:
                return ActionResult(False, f"目标文件夹不存在: #{parent_id}")
            if parent["course_id"] != folder["course_id"]:
                return ActionResult(False, "目标文件夹不属于该课程")
            if parent_id in self.storage.folder_descendants(folder_id):
                return ActionResult(False, "不能移入自己的子文件夹")
        self.storage.folder_set_parent(folder_id, parent_id)
        self.storage.log_operation("folder_move",
                                   params={"parent_id": parent_id},
                                   path_snapshot=folder["name"])
        return ActionResult(True, f"已移动文件夹 {folder['name']}")

    def resource_move(self, resource_id: int, folder_id) -> ActionResult:
        """把索引条目移入文件夹(None=课程根), 只动索引."""
        res = self.storage.get_resource(resource_id)
        if res is None:
            return ActionResult(False, f"资源不存在: #{resource_id}")
        if folder_id is not None:
            folder = self.storage.get_folder(folder_id)
            if folder is None:
                return ActionResult(False, f"目标文件夹不存在: #{folder_id}")
            if folder["course_id"] != res["course_id"]:
                return ActionResult(False, "目标文件夹不属于该课程")
        if folder_id == res["folder_id"]:
            return ActionResult(False, "已在该文件夹")
        old = res["folder_id"]
        self.storage.set_resource_folder(resource_id, folder_id)
        self.storage.log_operation(
            "resource_move", resource_id, params={"folder_id": folder_id,
                                                  "from": old},
            path_snapshot=res["path_or_url"])
        return ActionResult(True, "已移动索引条目")
