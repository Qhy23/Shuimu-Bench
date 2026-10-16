"""controller.py 单元测试: 业务动作层(不依赖 Qt)."""

import pytest

from shuimu.controller import Controller, detect_kind
from shuimu.storage import Storage


@pytest.fixture()
def ctl(tmp_path):
    """每个测试用独立的 controller + 临时数据库."""
    storage = Storage(str(tmp_path / "ctl.db"))
    yield Controller(storage)
    storage.close()


class TestDetectKind:
    """资源类型判断."""

    def test_kinds(self, tmp_path):
        """http 链接为 link, 目录为 dir, 文件为 file."""
        assert detect_kind("https://x.com") == "link"
        assert detect_kind(str(tmp_path)) == "dir"
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert detect_kind(str(f)) == "file"


class TestCourseActions:
    """课程动作."""

    def test_add_course(self, ctl):
        """新建课程成功并在结果中带回 id."""
        result = ctl.add_course("数学")
        assert result.ok and "数学" in result.message
        assert result.detail["course_id"] > 0

    def test_add_empty_name_rejected(self, ctl):
        """空课程名被拒绝且不产生课程."""
        result = ctl.add_course("   ")
        assert not result.ok
        assert len(ctl.storage.list_courses()) == 1  # 仅默认"未分组"

    def test_delete_course(self, ctl, tmp_path):
        """删除课程级联移除索引并留痕."""
        cid = ctl.add_course("临时").detail["course_id"]
        f = tmp_path / "a.txt"
        f.write_text("x")
        ctl.add_resource(cid, str(f))
        result = ctl.delete_course(cid)
        assert result.ok and result.detail["removed_resources"] == 1
        assert f.exists()
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "delete_course"

    def test_delete_missing_course(self, ctl):
        """删除不存在的课程返回失败, 不留痕."""
        assert not ctl.delete_course(999).ok
        assert ctl.storage.list_operations() == []


class TestResourceActions:
    """资源动作."""

    def test_add_file(self, ctl, tmp_path):
        """登记文件成功, 留痕 add_resource."""
        cid = ctl.storage.list_courses()[0]["id"]
        f = tmp_path / "a.pdf"
        f.write_text("x")
        result = ctl.add_resource(cid, str(f))
        assert result.ok and result.detail["kind"] == "file"
        assert ctl.storage.list_operations()[0]["action"] == "add_resource"

    def test_add_missing_path(self, ctl):
        """不存在的路径返回失败且不入库."""
        cid = ctl.storage.list_courses()[0]["id"]
        assert not ctl.add_resource(cid, "/nonexistent/xyz").ok
        assert ctl.storage.list_resources(cid) == []

    def test_remove_resource(self, ctl, tmp_path):
        """移除索引不动磁盘文件."""
        cid = ctl.storage.list_courses()[0]["id"]
        f = tmp_path / "a.txt"
        f.write_text("x")
        rid = ctl.add_resource(cid, str(f)).detail["resource_id"]
        assert ctl.remove_resource(rid).ok
        assert f.exists()


class TestOrganizeActions:
    """整理动作."""

    def test_preview_then_execute(self, ctl, tmp_path):
        """预览返回计划, 执行后移动并留痕."""
        (tmp_path / "a.pdf").write_text("x")
        preview = ctl.preview_organize(str(tmp_path))
        assert preview.ok and len(preview.detail["plan"]) == 1
        result = ctl.execute_organize(str(tmp_path), preview.detail["plan"])
        assert result.ok
        assert (tmp_path / "文档" / "a.pdf").exists()
        assert ctl.storage.list_operations()[0]["action"] == "organize"

    def test_preview_bad_dir(self, ctl):
        """非法目录预览失败."""
        assert not ctl.preview_organize("/nonexistent/xyz").ok

    def test_cancel_logs_cancelled(self, ctl, tmp_path):
        """取消整理留痕为 cancelled."""
        ctl.cancel_organize(str(tmp_path))
        assert ctl.storage.list_operations()[0]["status"] == "cancelled"
