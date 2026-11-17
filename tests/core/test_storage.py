"""storage.py 单元测试: 课程, 资源索引, 失效/变更检测, 操作留痕, 学堂缓存."""

import os
import sqlite3

import pytest

from shuimu.core.learn import (
    LearnCourse,
    LearnFile,
    LearnHomework,
    LearnNotice,
)
from shuimu.core.storage import Storage


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


class TestFolder:
    """虚拟文件夹: 组织层 CRUD 与级联."""

    def test_crud_and_sibling_name_rejected(self, storage, course_id):
        """建/查/改名/兄弟重名拒绝."""
        fid = storage.add_folder(course_id, None, "课件")
        assert storage.get_folder(fid)["name"] == "课件"
        sub = storage.add_folder(course_id, fid, "第1周")
        assert storage.list_folders(course_id)[0]["id"] == fid
        storage.folder_rename(sub, "第一周")
        assert storage.get_folder(sub)["name"] == "第一周"
        with pytest.raises(ValueError, match="同名"):
            storage.add_folder(course_id, None, "课件")
        with pytest.raises(ValueError, match="同名"):
            storage.add_folder(course_id, fid, "第一周")   # 与改后的子项同名

    def test_delete_empty_only_and_tree(self, storage, course_id):
        """空校验删除; 树删除级联子文件夹, 资源行随清."""
        fid = storage.add_folder(course_id, None, "课件")
        sub = storage.add_folder(course_id, fid, "第1周")
        with pytest.raises(ValueError, match="非空"):
            storage.folder_delete(fid)
        storage.folder_delete_tree(fid)          # 级联删除子文件夹
        assert storage.get_folder(fid) is None
        assert storage.get_folder(sub) is None

    def test_descendants_and_resource_placement(self, storage, course_id):
        """子孙枚举(BFS)与资源归属/移动/批量清理."""
        f1 = storage.add_folder(course_id, None, "A")
        f2 = storage.add_folder(course_id, f1, "B")
        f3 = storage.add_folder(course_id, f2, "C")
        assert storage.folder_descendants(f1) == [f1, f2, f3]
        rid = storage.add_resource(course_id, "link", "https://x/", folder_id=f3)
        assert storage.get_resource(rid)["folder_id"] == f3
        storage.set_resource_folder(rid, None)
        assert storage.get_resource(rid)["folder_id"] is None
        storage.set_resource_folder(rid, f2)
        assert storage.remove_resources_in_folders([f1, f2]) == 1
        assert storage.get_resource(rid) is None

    def test_old_schema_migrated(self, tmp_path):
        """旧库(无 folders/folder_id)打开即迁移, 原数据完好."""
        db = str(tmp_path / "old.db")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE courses (id INTEGER PRIMARY KEY,
              name TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
            CREATE TABLE resources (id INTEGER PRIMARY KEY,
              course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
              kind TEXT NOT NULL, path_or_url TEXT NOT NULL,
              display_name TEXT, size INTEGER, mtime REAL,
              added_at REAL NOT NULL,
              UNIQUE (course_id, path_or_url));
        """)
        conn.execute("INSERT INTO courses VALUES (1, '旧课程', 0)")
        conn.execute("INSERT INTO resources (course_id, kind, path_or_url,"
                     " display_name, added_at) VALUES (1, 'link', 'https://x/',"
                     " 'x', 0)")
        conn.commit()
        conn.close()
        store = Storage(db)
        cols = {row[1] for row in store._conn.execute(
            "PRAGMA table_info(resources)")}
        assert "folder_id" in cols
        assert len(store.list_folders(1)) == 0
        assert store.list_resources(1)[0]["path_or_url"] == "https://x/"
        store.close()


class TestMemoryDb:
    """":memory:" 特殊路径: 真内存库, 不在当前目录落文件."""

    def test_memory_db_creates_no_file(self, tmp_path, monkeypatch):
        """建库/建表/闭库全程不在 cwd 产生 ":memory:" 文件."""
        monkeypatch.chdir(tmp_path)
        store = Storage(":memory:")
        store.add_course("课程")
        assert store.list_courses()[0]["name"] == "课程"
        store.close()
        assert not (tmp_path / ":memory:").exists()
        assert not list(tmp_path.iterdir())


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

    def test_relative_path_stored_absolute(self, storage, course_id, tmp_path,
                                           monkeypatch):
        """相对路径登记后转为绝对路径, 换工作目录后校验仍有效."""
        f = tmp_path / "rel.txt"
        f.write_text("x")
        monkeypatch.chdir(tmp_path)
        rid = storage.add_resource(course_id, "file", "rel.txt")
        row = storage.get_resource(rid)
        assert os.path.isabs(row["path_or_url"])
        monkeypatch.chdir("/")
        exists, _ = Storage.check_resource(row)
        assert exists


class TestSearchResources:
    """跨课程关键词搜索."""

    def test_match_name_or_path(self, storage, course_id, tmp_path):
        """显示名或路径任一含关键词即命中, 结果带课程名."""
        other = storage.add_course("人工智能")
        f1 = tmp_path / "machine_learning.pdf"
        f1.write_text("x")
        deep = tmp_path / "deep"
        deep.mkdir()
        f2 = deep / "notes.txt"
        f2.write_text("x")
        storage.add_resource(course_id, "file", str(f1))
        storage.add_resource(other, "file", str(f2))
        rows = storage.search_resources("learning")
        assert [r["display_name"] for r in rows] == ["machine_learning.pdf"]
        rows = storage.search_resources("deep")
        assert [r["display_name"] for r in rows] == ["notes.txt"]  # 仅路径命中
        assert rows[0]["course_name"] == "人工智能"

    def test_case_insensitive(self, storage, course_id, tmp_path):
        """英文匹配不区分大小写."""
        f = tmp_path / "Report.PDF"
        f.write_text("x")
        storage.add_resource(course_id, "file", str(f))
        assert len(storage.search_resources("report")) == 1
        assert len(storage.search_resources("PDF")) == 1

    def test_like_wildcards_literal(self, storage, course_id, tmp_path):
        """% 与 _ 按字面匹配, 不当通配符."""
        for name in ("100%_done.txt", "100x_done.txt",
                     "my notes.txt", "my_notes.txt"):
            f = tmp_path / name
            f.write_text("x")
            storage.add_resource(course_id, "file", str(f))
        rows = storage.search_resources("100%")
        assert [r["display_name"] for r in rows] == ["100%_done.txt"]
        rows = storage.search_resources("my_notes")
        assert [r["display_name"] for r in rows] == ["my_notes.txt"]

    def test_missing_and_link_searchable(self, storage, course_id, tmp_path):
        """失效资源与链接均可被搜到."""
        f = tmp_path / "gone.txt"
        f.write_text("x")
        rid = storage.add_resource(course_id, "file", str(f))
        storage.add_resource(course_id, "link", "https://learn.tsinghua.edu.cn")
        f.unlink()
        rows = storage.search_resources("gone")
        assert [r["id"] for r in rows] == [rid]
        assert len(storage.search_resources("tsinghua")) == 1

    def test_no_match(self, storage):
        """无命中返回空列表."""
        assert storage.search_resources("不存在的关键词") == []


class TestDeleteCourse:
    """删除课程: 级联移除资源索引, 磁盘文件不动."""

    def test_cascade_removes_resources(self, storage, tmp_path):
        """删课程后其资源索引一并消失(依赖外键 CASCADE), 磁盘文件保留."""
        cid = storage.add_course("临时课")
        f = tmp_path / "keep.txt"
        f.write_text("x")
        rid = storage.add_resource(cid, "file", str(f))
        removed = storage.delete_course(cid)
        assert removed == 1
        assert storage.get_course(cid) is None
        assert storage.get_resource(rid) is None
        assert f.exists()

    def test_other_courses_unaffected(self, storage, course_id, tmp_path):
        """删一门课不影响其他课程的资源."""
        f = tmp_path / "x.txt"
        f.write_text("x")
        rid = storage.add_resource(course_id, "file", str(f))
        other = storage.add_course("别的课")
        storage.delete_course(other)
        assert storage.get_resource(rid) is not None


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


class TestLearnCache:
    """网络学堂缓存表: 差分同步与保留列."""

    def _courses(self):
        return [LearnCourse(wlkcid="W1", kcm="AI 编程", kch="CS1"),
                LearnCourse(wlkcid="W2", kcm="核仪器", kch="CS2")]

    def test_course_sync_new_then_upsert_keeps_link(self, storage):
        """首刷入库; 二刷更新字段且保留本地课程关联; 消失课程移除."""
        local_id = storage.add_course("本地课")
        diff = storage.learn_sync_courses("2026-2027-1", self._courses())
        assert [item[0] for item in diff["new"]] == ["W1", "W2"]
        assert not diff["removed"]
        storage.learn_set_course_link("W1", local_id)
        diff = storage.learn_sync_courses("2026-2027-1", self._courses()[:1])
        assert not diff["new"] and diff["removed"] == ["W2"]
        rows = {row["wlkcid"]: row for row in storage.learn_list_courses()}
        assert rows["W1"]["course_id"] == local_id   # 关联保留
        assert set(rows) == {"W1"}

    def test_course_removal_cascades_children(self, storage):
        """删除学堂课程时级联清理其作业/公告/课件."""
        storage.learn_sync_courses("2026-2027-1", self._courses())
        storage.learn_sync_homework(
            [LearnHomework(wlkcid="W2", zyid="Z1", state="pending")])
        storage.learn_sync_courses("2026-2027-1", self._courses()[:1])
        assert storage.learn_list_homework(wlkcid="W2") == []
        assert storage.learn_list_homework(wlkcid="W1") == []

    def test_semester_switch_clears_old(self, storage):
        """学期切换后旧学期行全部移除(表内只保留最新学期)."""
        storage.learn_sync_courses("2026-2027-1", self._courses())
        diff = storage.learn_sync_courses(
            "2027-2028-1", self._courses()[:1])
        assert diff["removed"] == ["W2"]
        rows = storage.learn_list_courses()
        assert len(rows) == 1 and rows[0]["semester_id"] == "2027-2028-1"

    def test_homework_diff_and_kept_columns(self, storage):
        """新作业入 new; 状态变化入 changed; 描述缓存与待办映射保留."""
        storage.learn_sync_courses("2026-2027-1", self._courses()[:1])
        diff = storage.learn_sync_homework(
            [LearnHomework(wlkcid="W1", zyid="Z1", state="pending",
                           bt="作业一")])
        assert diff["new"] == [("W1", "Z1", "作业一")]
        storage.learn_save_homework_detail("W1", "Z1", "<p>x</p>")
        storage.learn_set_homework_todo("W1", "Z1", 7)
        diff = storage.learn_sync_homework(
            [LearnHomework(wlkcid="W1", zyid="Z1", state="submitted",
                           bt="作业一")])
        assert diff["changed"] == [("W1", "Z1", "作业一",
                                    "pending", "submitted")]
        row = storage.learn_list_homework(wlkcid="W1")[0]
        assert row["state"] == "submitted"
        assert row["detail_html"] == "<p>x</p>"   # 保留
        assert row["todo_tid"] == 7               # 保留

    def test_notice_and_file_upsert(self, storage):
        """公告/课件 upsert 与课件分类回填."""
        storage.learn_sync_courses("2026-2027-1", self._courses()[:1])
        diff = storage.learn_sync_notices(
            [LearnNotice(wlkcid="W1", ggid="G1", bt="公告",
                         body_html="<p>hi</p>", sfyd=True)])
        assert diff["new"] == [("W1", "G1", "公告")]
        assert storage.learn_sync_notices(
            [LearnNotice(wlkcid="W1", ggid="G1", bt="公告",
                         sfyd=True)])["new"] == []
        notice = storage.learn_list_notices("W1")[0]
        assert notice["ggnr"] == "<p>hi</p>" and notice["sfyd"] == "1"

        diff = storage.learn_sync_files(
            [LearnFile(wlkcid="W1", kjxxid="K1", wjid="WJ1", bt="讲义",
                       wjdx=10, kjflid="C1")], {"C1": "电子教案"})
        assert diff["new"] == [("W1", "K1", "讲义")]
        row = storage.learn_find_file("WJ1")
        assert row["category"] == "电子教案"
        assert storage.learn_sync_files(
            [LearnFile(wlkcid="W1", kjxxid="K1", wjid="WJ1", bt="讲义")])[
                "new"] == []

    def test_download_record_and_meta(self, storage):
        """下载记录 upsert; 元信息键值."""
        local = storage.ensure_default_course()
        rid = storage.add_resource(local, "file", "/tmp/a.pdf")
        storage.learn_record_download("file", "WJ1", "W1", "/tmp/a.pdf", rid)
        storage.learn_record_download("file", "WJ1", "W1", "/tmp/b.pdf")
        row = storage.learn_find_download("file", "WJ1")
        assert row["path"] == "/tmp/b.pdf" and row["resource_id"] is None
        assert storage.learn_find_download("file", "WJX") is None
        assert storage.learn_meta_get("k") is None
        storage.learn_meta_set("k", "v")
        storage.learn_meta_set("k", "v2")
        assert storage.learn_meta_get("k") == "v2"

    def test_old_db_migrates(self, tmp_path):
        """旧三表库打开后自动建学堂表, 原数据不受影响."""
        db = str(tmp_path / "old.db")
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT"
            " NOT NULL UNIQUE, created_at REAL NOT NULL);"
            "INSERT INTO courses (name, created_at) VALUES ('旧课', 0);")
        conn.commit()
        conn.close()
        store = Storage(db)
        assert store.learn_meta_get("k") is None    # 新表可用
        store.learn_meta_set("k", "v")
        assert [row["name"] for row in store.list_courses()] == ["旧课"]
        store.close()
