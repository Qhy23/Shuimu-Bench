"""folder_actions.py 单元测试: 虚拟文件夹动作与目录导入."""

from tests.core.test_controller import ctl  # noqa: F401  (fixture)
class TestFolderActions:
    """虚拟文件夹动作: 增删改移与留痕."""

    def test_create_rename_delete(self, ctl):
        """建/改名/删全链路留痕; 改名同名拒绝."""
        cid = ctl.storage.list_courses()[0]["id"]
        result = ctl.folder_create(cid, None, "课件")
        fid = result.detail["folder_id"]
        assert result.ok
        assert not ctl.folder_rename(fid, "课件").ok      # 未变化
        assert ctl.folder_rename(fid, "讲义").ok
        assert ctl.folder_delete(fid).ok                  # 空文件夹直删
        assert not ctl.folder_delete(fid).ok              # 已不存在
        actions = [op["action"] for op in ctl.storage.list_operations()]
        assert actions == ["folder_delete",          # 最新在前; 不存在
                           "folder_rename",           # 的那次不留痕
                           "folder_create"]

    def test_delete_nonempty_strict_and_cascade(self, ctl, tmp_path):
        """非空: 严格模式拒绝, cascade 连子文件夹与索引一并清(磁盘不动)."""
        cid = ctl.storage.list_courses()[0]["id"]
        f1 = ctl.storage.add_folder(cid, None, "课件")
        f2 = ctl.storage.add_folder(cid, f1, "第1周")
        f = tmp_path / "a.txt"
        f.write_text("x")
        ctl.add_resource(cid, str(f), folder_id=f2)
        ctl.add_resource(cid, "https://x/", folder_id=f1)
        denied = ctl.folder_delete(f1)
        assert not denied.ok and "非空" in denied.message
        result = ctl.folder_delete(f1, cascade=True)
        assert result.ok and result.detail["removed_resources"] == 2
        assert ctl.storage.list_folders(cid) == []
        assert ctl.storage.list_resources(cid) == []
        assert f.exists()                                  # 磁盘不动

    def test_folder_move_ring_detection(self, ctl):
        """移入自身/子孙/他课文件夹均拒绝."""
        cid = ctl.storage.list_courses()[0]["id"]
        other = ctl.add_course("另一门").detail["course_id"]
        f1 = ctl.storage.add_folder(cid, None, "A")
        f2 = ctl.storage.add_folder(cid, f1, "B")
        f_other = ctl.storage.add_folder(other, None, "X")
        assert not ctl.folder_move(f1, f1).ok
        assert not ctl.folder_move(f1, f2).ok               # 移入子孙
        assert not ctl.folder_move(f1, f_other).ok          # 跨课程
        assert ctl.folder_move(f2, None).ok                 # 移回课程根

    def test_resource_move_and_add_with_folder(self, ctl, tmp_path):
        """索引条目移动校验; add_resource 带文件夹落位."""
        cid = ctl.storage.list_courses()[0]["id"]
        other = ctl.add_course("另一门").detail["course_id"]
        fid = ctl.storage.add_folder(cid, None, "课件")
        f = tmp_path / "a.txt"
        f.write_text("x")
        rid = ctl.add_resource(cid, str(f), folder_id=fid).detail["resource_id"]
        assert ctl.storage.get_resource(rid)["folder_id"] == fid
        assert not ctl.add_resource(cid, str(f), folder_id=fid).ok  # 重复登记
        f_other = ctl.storage.add_folder(other, None, "X")
        assert not ctl.resource_move(rid, f_other).ok       # 跨课程
        assert not ctl.resource_move(rid, fid).ok           # 已在该文件夹
        assert ctl.resource_move(rid, None).ok              # 移回课程根


class TestDirectoryImport:
    """目录导入: 递归建虚拟文件夹 + 文件索引, 不建目录索引."""

    def test_import_structure_and_logging(self, ctl, tmp_path):
        """嵌套目录导入为层级文件夹, 文件落在对应层; 单条留痕."""
        src = tmp_path / "课件"
        (src / "第1周").mkdir(parents=True)
        (src / "README.md").write_text("x")
        (src / "第1周" / "a.pdf").write_text("y")
        (src / "第1周" / "b.pdf").write_text("z")
        cid = ctl.storage.list_courses()[0]["id"]
        result = ctl.import_directory(cid, str(src))
        assert result.ok
        assert result.detail["folders"] == 2
        assert result.detail["files"] == 3
        folders = {f["name"]: f for f in ctl.storage.list_folders(cid)}
        root = ctl.storage.get_folder(result.detail["folder_id"])
        assert root["name"] == "课件" and root["parent_id"] is None
        assert folders["第1周"]["parent_id"] == root["id"]
        rid = ctl.storage.find_resource(cid, str(src / "README.md"))
        assert rid["folder_id"] == root["id"]
        rid_b = ctl.storage.find_resource(cid, str(src / "第1周" / "b.pdf"))
        assert rid_b["folder_id"] == folders["第1周"]["id"]
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "import_directory"   # 单条留痕
        assert len([o for o in ops if o["action"] == "add_resource"]) == 0

    def test_import_into_folder_and_skip_registered(self, ctl, tmp_path):
        """落点可为指定文件夹; 同课程已登记文件跳过."""
        cid = ctl.storage.list_courses()[0]["id"]
        target = ctl.storage.add_folder(cid, None, "资料")
        src = tmp_path / "课件"
        src.mkdir()
        (src / "a.pdf").write_text("x")
        (src / "b.pdf").write_text("y")
        ctl.add_resource(cid, str(src / "a.pdf"))       # 预登记一个
        result = ctl.import_directory(cid, str(src), folder_id=target)
        assert result.ok and result.detail["skipped"] == 1
        assert result.detail["files"] == 1
        root = ctl.storage.get_folder(result.detail["folder_id"])
        assert root["parent_id"] == target

    def test_import_rejects(self, ctl, tmp_path):
        """非目录拒绝; 同名顶层文件夹拒绝; 无效目标文件夹拒绝."""
        cid = ctl.storage.list_courses()[0]["id"]
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert not ctl.import_directory(cid, str(f)).ok
        src = tmp_path / "课件"
        src.mkdir()
        assert ctl.import_directory(cid, str(src)).ok
        assert not ctl.import_directory(cid, str(src)).ok    # 同名冲突
        assert not ctl.import_directory(cid, str(src), folder_id=999).ok
