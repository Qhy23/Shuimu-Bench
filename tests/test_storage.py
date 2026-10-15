"""storage.py 单元测试: 课程, 资源索引, 失效/变更检测, 操作留痕."""

import os

import pytest

from shuimu.storage import Storage


@pytest.fixture()
def storage(tmp_path):
    """每个测试用独立的临时数据库."""
    store = Storage(str(tmp_path / "test.db"))
    yield store
    store.close()


@pytest.fixture()
def course_id(storage):
    """一门默认课程."""
    return storage.ensure_default_course()


class TestCourse:
    """课程表行为."""

    def test_default_course_created_once(self, storage, course_id):
        """默认课程只创建一次, 重复调用返回同一 id."""
        assert storage.ensure_default_course() == course_id
        assert len(storage.list_courses()) == 1

    def test_add_course(self, storage, course_id):
        """新建课程出现在列表末尾."""
        assert storage.list_courses()[0]["id"] == course_id
        storage.add_course("高等数学")
        names = [c["name"] for c in storage.list_courses()]
        assert names == ["未分组", "高等数学"]


class TestResource:
    """资源索引行为."""

    def test_add_file_records_stat(self, storage, course_id, tmp_path):
        """登记文件时记录 size/mtime, 校验为存在且未变更."""
        f = tmp_path / "a.pdf"
        f.write_bytes(b"pdf")
        rid = storage.add_resource(course_id, "file", str(f))
        row = storage.get_resource(rid)
        assert row["display_name"] == "a.pdf"
        assert row["size"] == 3
        exists, changed = Storage.check_resource(row)
        assert exists and not changed

    def test_link_always_ok(self, storage, course_id):
        """链接资源恒为存在且未变更."""
        rid = storage.add_resource(course_id, "link", "https://example.com")
        exists, changed = Storage.check_resource(storage.get_resource(rid))
        assert exists and not changed

    def test_missing_file_detected(self, storage, course_id, tmp_path):
        """文件被删除后懒校验报告失效."""
        f = tmp_path / "gone.txt"
        f.write_text("x")
        rid = storage.add_resource(course_id, "file", str(f))
        f.unlink()
        exists, _ = Storage.check_resource(storage.get_resource(rid))
        assert not exists

    def test_modified_file_detected(self, storage, course_id, tmp_path):
        """文件内容或 mtime 变化后报告已变更."""
        f = tmp_path / "m.txt"
        f.write_text("v1")
        rid = storage.add_resource(course_id, "file", str(f))
        f.write_text("v2-longer")
        os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 10))
        exists, changed = Storage.check_resource(storage.get_resource(rid))
        assert exists and changed

    def test_remove_resource_keeps_file(self, storage, course_id, tmp_path):
        """移除索引不动磁盘文件."""
        f = tmp_path / "keep.txt"
        f.write_text("x")
        rid = storage.add_resource(course_id, "file", str(f))
        storage.remove_resource(rid)
        assert storage.get_resource(rid) is None
        assert f.exists()

    def test_duplicate_rejected(self, storage, course_id, tmp_path):
        """同一课程重复登记同一路径被拒绝."""
        f = tmp_path / "dup.txt"
        f.write_text("x")
        storage.add_resource(course_id, "file", str(f))
        with pytest.raises(Exception):
            storage.add_resource(course_id, "file", str(f))


class TestOperations:
    """操作留痕."""

    def test_log_and_list(self, storage):
        """留痕可写入并读回, dict 参数转 JSON."""
        storage.log_operation(
            "organize", params={"directory": "/tmp/x"},
            path_snapshot="/tmp/x", detail={"moved": 3})
        ops = storage.list_operations()
        assert len(ops) == 1
        assert ops[0]["action"] == "organize"
        assert ops[0]["status"] == "ok"
        assert '"moved": 3' in ops[0]["detail"]

    def test_cancelled_status(self, storage):
        """取消状态可正常记录."""
        storage.log_operation("organize", status="cancelled")
        assert storage.list_operations()[0]["status"] == "cancelled"
