"""controller.py 单元测试: 业务动作层(不依赖 Qt)."""

import json
import os
from copy import deepcopy
from datetime import date, time

import pytest

from shuimu.core.config import DEFAULTS
from shuimu.core.controller import Controller, detect_kind
from shuimu.core.quicklinks import QuickLink
from shuimu.core.schedule import Event, save_schedule
from shuimu.core.storage import Storage


@pytest.fixture()
def ctl(tmp_path, monkeypatch):
    """每个测试用独立的 controller + 临时数据库 + 临时日程/链接文件(模板隔离)."""
    monkeypatch.setattr("shuimu.core.controller.template_schedule_path",
                        lambda: str(tmp_path / "no-such-template.yaml"))
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-such-links-template.yaml"))
    storage = Storage(str(tmp_path / "ctl.db"))
    yield Controller(storage, schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(tmp_path / "links.yaml"))
    storage.close()


@pytest.fixture()
def ctl_trash(tmp_path, monkeypatch):
    """带独立 trash_dir 配置的 controller(删除测试不碰真实回收站)."""
    monkeypatch.setattr("shuimu.core.controller.template_schedule_path",
                        lambda: str(tmp_path / "no-such-template.yaml"))
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-such-links-template.yaml"))
    cfg = deepcopy(DEFAULTS)
    cfg["fileops"]["trash_dir"] = str(tmp_path / "trash")
    storage = Storage(str(tmp_path / "ctl.db"))
    yield Controller(storage, config=cfg,
                     schedule_path=str(tmp_path / "s.yaml"),
                     links_path=str(tmp_path / "links.yaml"))
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


class TestSearchActions:
    """跨课程搜索动作(只读)."""

    def test_search_rows_with_course_name(self, ctl, tmp_path):
        """命中行带课程名, 消息含数量."""
        cid = ctl.add_course("物理").detail["course_id"]
        default = ctl.storage.list_courses()[0]["id"]
        f1 = tmp_path / "lecture.txt"
        f1.write_text("x")
        d = tmp_path / "实验数据"
        d.mkdir()
        f2 = d / "raw.csv"
        f2.write_text("x")
        ctl.add_resource(cid, str(f1))
        ctl.add_resource(default, str(f2))
        result = ctl.search_resources("实验")
        assert result.ok and "找到 1 条匹配" in result.message
        rows = result.detail["rows"]
        assert len(rows) == 1 and rows[0]["course_name"] == "未分组"

    def test_search_read_only(self, ctl, tmp_path):
        """搜索不新增操作留痕."""
        cid = ctl.storage.list_courses()[0]["id"]
        f = tmp_path / "a.txt"
        f.write_text("x")
        ctl.add_resource(cid, str(f))
        before = len(ctl.storage.list_operations())
        assert ctl.search_resources("a.txt").ok
        assert len(ctl.storage.list_operations()) == before

    def test_search_blank_keyword(self, ctl):
        """空白关键词返回失败."""
        assert not ctl.search_resources("   ").ok


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


class TestFileOpActions:
    """文件操作动作(复制/移动/删除/重命名/新建)."""

    def test_copy(self, ctl, tmp_path):
        """复制两项成功留痕, detail 含逐条结果."""
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.txt").write_text("a")
        (src / "b.txt").write_text("b")
        result = ctl.file_copy([str(src / "a.txt"), str(src / "b.txt")], str(dst))
        assert result.ok
        assert (dst / "a.txt").exists() and (dst / "b.txt").exists()
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_copy" and op["status"] == "ok"
        detail = json.loads(op["detail"])
        assert len(detail["items"]) == 2
        assert detail["renamed"] == 0

    def test_copy_conflict_renamed(self, ctl, tmp_path):
        """目标重名自动改名并计入 renamed."""
        src, dst = tmp_path / "src", tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.txt").write_text("new")
        (dst / "a.txt").write_text("old")
        result = ctl.file_copy([str(src / "a.txt")], str(dst))
        assert result.ok and "1 项重名自动改名" in result.message
        assert (dst / "a_1.txt").read_text() == "new"
        assert json.loads(ctl.storage.list_operations()[0]["detail"])["renamed"] == 1

    def test_copy_partial_failure(self, ctl, tmp_path):
        """源缺失单条失败, 整体 not ok 且留痕 status=failed."""
        dst = tmp_path / "dst"
        dst.mkdir()
        good = tmp_path / "g.txt"
        good.write_text("x")
        result = ctl.file_copy([str(tmp_path / "nope"), str(good)], str(dst))
        assert not result.ok and (dst / "g.txt").exists()
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_copy" and op["status"] == "failed"
        assert json.loads(op["detail"])["items"][0]["status"] == "failed"

    def test_invalid_input_no_log(self, ctl, tmp_path):
        """空 sources 或目标目录不存在: 失败且不留痕."""
        before = len(ctl.storage.list_operations())
        assert not ctl.file_copy([], str(tmp_path)).ok
        assert not ctl.file_copy([str(tmp_path)], str(tmp_path / "nope")).ok
        assert len(ctl.storage.list_operations()) == before

    def test_move_dir_into_itself(self, ctl, tmp_path):
        """目录移入自身内部失败且原位不动."""
        d = tmp_path / "d"
        (d / "in").mkdir(parents=True)
        (d / "in" / "x.txt").write_text("x")
        result = ctl.file_move([str(d)], str(d / "in"))
        assert not result.ok
        assert (d / "in" / "x.txt").exists()
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_move" and op["status"] == "failed"

    def test_delete_to_trash(self, ctl_trash, tmp_path):
        """删除入回收站, 留痕含原路径->回收站路径映射与后端名."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        result = ctl_trash.file_delete([str(f)])
        assert result.ok and not f.exists()
        op = ctl_trash.storage.list_operations()[0]
        assert op["action"] == "file_delete" and op["status"] == "ok"
        item = json.loads(op["detail"])["items"][0]
        assert item["src"] == str(f)
        assert item["backend"] == "config_dir"
        assert item["trash"].endswith("a.txt")

    def test_rename(self, ctl, tmp_path):
        """重命名留痕 path_snapshot 为旧路径, detail 为新路径."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        result = ctl.file_rename(str(f), "b.txt")
        assert result.ok and (tmp_path / "b.txt").exists()
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_rename"
        assert op["path_snapshot"] == str(f)
        assert json.loads(op["detail"])["dst"] == str(tmp_path / "b.txt")

    def test_rename_invalid_no_log(self, ctl, tmp_path):
        """目标已存在: 失败且不留痕."""
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        before = len(ctl.storage.list_operations())
        assert not ctl.file_rename(str(tmp_path / "a.txt"), "b.txt").ok
        assert len(ctl.storage.list_operations()) == before

    def test_mkdir(self, ctl, tmp_path):
        """新建文件夹重名自动改名, 消息与 detail 用实际名."""
        (tmp_path / "新建文件夹").mkdir()
        result = ctl.file_mkdir(str(tmp_path))
        assert result.ok and (tmp_path / "新建文件夹_1").is_dir()
        assert "新建文件夹_1" in result.message
        op = ctl.storage.list_operations()[0]
        assert op["action"] == "file_mkdir"
        assert json.loads(op["detail"])["name"] == "新建文件夹_1"


class TestScheduleActions:
    """日程动作."""

    def test_snapshot_seeds_lazily(self, ctl, tmp_path):
        """首次 snapshot 播种数据文件(模板缺失退化为骨架)."""
        path = tmp_path / "s.yaml"
        assert not path.exists()
        data = ctl.schedule_snapshot()
        assert path.exists() and data.warnings == []

    def test_add_todo(self, ctl):
        """新建待办落盘并留痕."""
        result = ctl.add_todo("  微积分作业  ", date(2026, 10, 26), "微积分")
        assert result.ok and result.detail["tid"] == 1
        data = ctl.schedule_snapshot()
        assert data.todos[0].title == "微积分作业"  # 已 strip
        assert data.todos[0].due == date(2026, 10, 26)
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "add_todo"
        assert json.loads(ops[0]["params"])["title"] == "微积分作业"

    def test_add_todo_blank_title(self, ctl):
        """空标题拒绝且不落盘."""
        assert not ctl.add_todo("   ").ok
        assert ctl.schedule_snapshot().todos == []

    def test_toggle_todo(self, ctl):
        """翻转完成状态写回并留痕; 不存在的 id 失败."""
        tid = ctl.add_todo("任务").detail["tid"]
        result = ctl.toggle_todo(tid)
        assert result.ok and result.detail["done"] is True
        assert ctl.schedule_snapshot().todos[0].done is True
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "toggle_todo"
        assert json.loads(ops[0]["detail"])["done"] is True
        assert not ctl.toggle_todo(999).ok

    def test_course_names(self, ctl):
        """课程名列表含默认课程."""
        assert "未分组" in ctl.course_names()

    def test_import_courses(self, ctl):
        """个人校历导入替换 courses 段, manual 与待办保留, 留痕含合并数."""
        fixture = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                               "portal_calendar_sample.html")
        ctl.add_todo("任务")
        data = ctl.schedule_snapshot()
        data.manual.append(Event(date(2026, 10, 27), time(9), time(10),
                                 "手动事件", ""))
        save_schedule(ctl.schedule_path, data)

        result = ctl.import_courses(fixture)
        assert result.ok and result.detail["courses"] == 3
        assert result.detail["merged"] == 1
        after = ctl.schedule_snapshot()
        assert len(after.courses) == 3
        assert after.courses[0].end == time(11, 25)  # 联排课合并
        assert len(after.manual) == 1 and len(after.todos) == 1
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "import_courses"
        detail = json.loads(ops[0]["detail"])
        assert detail["replaced"] == 0 and detail["merged"] == 1

    def test_import_courses_bad_file(self, ctl, tmp_path):
        """不存在的文件与无分节的 html 都返回失败."""
        assert not ctl.import_courses(str(tmp_path / "nope.html")).ok
        no_dates = tmp_path / "plain.html"
        no_dates.write_text("<html></html>", encoding="utf-8")
        assert not ctl.import_courses(str(no_dates)).ok


class TestSemesterConfig:
    """学期配置访问器."""

    def test_week_count_from_config(self, tmp_path, monkeypatch):
        """week_count 经配置注入, 非法值回退 0."""
        monkeypatch.setattr("shuimu.core.controller.template_schedule_path",
                            lambda: str(tmp_path / "no-tpl.yaml"))
        cfg = deepcopy(DEFAULTS)
        cfg["schedule"]["week_count"] = 18
        storage = Storage(str(tmp_path / "sem.db"))
        ctl = Controller(storage, config=cfg,
                         schedule_path=str(tmp_path / "s.yaml"))
        assert ctl.semester_week_count() == 18
        cfg["schedule"]["week_count"] = "bad"
        ctl2 = Controller(storage, config=cfg,
                          schedule_path=str(tmp_path / "s2.yaml"))
        assert ctl2.semester_week_count() == 0
        storage.close()


class TestManualActions:
    """手动日程增删改."""

    def _event(self, day, start_h=8, title="自习", sid=0):
        """快速构造手动事件."""
        return Event(date(2026, 10, day), time(start_h), time(start_h + 2),
                     title, "图书馆", sid)

    def test_add_manual_series(self, ctl):
        """多条事件共享新系列号, 单条不加系列."""
        series = [self._event(26), self._event(27), self._event(28)]
        result = ctl.add_manual(series)
        assert result.ok and result.detail["count"] == 3
        data = ctl.schedule_snapshot()
        assert [e.sid for e in data.manual] == [1, 1, 1]
        result = ctl.add_manual([self._event(29, title="单次")])
        assert result.ok
        assert ctl.schedule_snapshot().manual[-1].sid == 0
        assert any(op["action"] == "add_manual"
                   for op in ctl.storage.list_operations())

    def test_add_manual_empty(self, ctl):
        """空列表拒绝."""
        assert not ctl.add_manual([]).ok

    def test_update_event_single(self, ctl):
        """单次修改替换原条目并保留系列号."""
        ctl.add_manual([self._event(26), self._event(27)])
        old = ctl.schedule_snapshot().manual[0]
        new = Event(date(2026, 10, 26), time(14), time(16), "改期自习", "", 0)
        result = ctl.update_event("manual", old, new)
        assert result.ok and result.detail["updated"] == 1
        entries = ctl.schedule_snapshot().manual
        assert entries[0].title == "改期自习" and entries[0].sid == old.sid
        assert entries[1].title == "自习"  # 另一场次不动

    def test_update_event_series(self, ctl):
        """系列修改应用到全部场次, 日期不变."""
        ctl.add_manual([self._event(26), self._event(27), self._event(28)])
        old = ctl.schedule_snapshot().manual[0]
        new = Event(None, time(19, 20), time(20, 55), "晚间系列", "三教", 0)
        result = ctl.update_event("manual", old, new, whole_series=True)
        assert result.ok and result.detail["updated"] == 3
        entries = ctl.schedule_snapshot().manual
        assert all(e.title == "晚间系列" for e in entries)
        assert [e.date.day for e in entries] == [26, 27, 28]  # 日期保持
        assert entries[0].start == time(19, 20)

    def test_update_event_not_found(self, ctl):
        """定位不到原条目返回失败."""
        ghost = self._event(26)
        assert not ctl.update_event("manual", ghost, ghost).ok
        assert not ctl.update_event("nowhere", ghost, ghost).ok

    def test_remove_event_single_and_series(self, ctl):
        """删本次只移一条, 删系列移全部."""
        ctl.add_manual([self._event(26), self._event(27), self._event(28)])
        data = ctl.schedule_snapshot()
        result = ctl.remove_event("manual", data.manual[0])
        assert result.ok and result.detail["removed"] == 1
        remaining = ctl.schedule_snapshot().manual
        assert len(remaining) == 2
        result = ctl.remove_event("manual", remaining[0], whole_series=True)
        assert result.ok and result.detail["removed"] == 2
        assert ctl.schedule_snapshot().manual == []

    def test_remove_event_not_found(self, ctl):
        """定位不到条目返回失败."""
        ghost = self._event(26)
        assert not ctl.remove_event("manual", ghost).ok


class TestQuickLinkActions:
    """常用链接动作."""

    def test_list_seeds_lazily(self, ctl, tmp_path):
        """首次读取播种数据文件(模板缺失退化为骨架), 列表为空."""
        path = tmp_path / "links.yaml"
        assert not path.exists()
        assert not ctl.list_quick_links()
        assert path.exists()

    def test_add(self, ctl):
        """添加落盘并留痕(params/detail 为 JSON)."""
        result = ctl.add_quick_link("  学堂  ", "  https://learn.example.com  ",
                                    " 信息化服务 ")
        assert result.ok
        links = ctl.list_quick_links()
        assert links == [QuickLink("学堂", "https://learn.example.com",
                                   "信息化服务")]
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "add_quick_link"
        assert json.loads(ops[0]["params"]) == {"name": "学堂"}
        assert json.loads(ops[0]["detail"]) == {
            "url": "https://learn.example.com", "group": "信息化服务"}

    def test_add_without_group(self, ctl):
        """group 缺省为未分组, 落盘条目不带 group 键."""
        ctl.add_quick_link("散链", "https://misc.example.com")
        assert ctl.list_quick_links() == [
            QuickLink("散链", "https://misc.example.com", "")]

    def test_add_rejects_bad_input(self, ctl):
        """重名/空名/非 http 前缀拒绝且不留痕不落盘."""
        ctl.add_quick_link("学堂", "https://learn.example.com")
        before = len(ctl.storage.list_operations())
        assert not ctl.add_quick_link("学堂", "https://other.example.com").ok
        assert not ctl.add_quick_link("  ", "https://x.example.com").ok
        assert not ctl.add_quick_link("门户", "info.example.com").ok
        assert len(ctl.storage.list_operations()) == before
        assert len(ctl.list_quick_links()) == 1

    def test_update(self, ctl):
        """改名改址改组写回并留痕, 改名时与新名冲突拒绝."""
        ctl.add_quick_link("学堂", "https://learn.example.com", "信息化服务")
        ctl.add_quick_link("门户", "https://info.example.com", "信息门户")
        result = ctl.update_quick_link("学堂", "网络学堂",
                                       "https://learn2.example.com",
                                       "资源查找")
        assert result.ok
        links = ctl.list_quick_links()
        assert links[0] == QuickLink("网络学堂", "https://learn2.example.com",
                                     "资源查找")
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "update_quick_link"
        assert ops[0]["path_snapshot"] == "学堂"
        assert json.loads(ops[0]["detail"])["group"] == "资源查找"
        assert not ctl.update_quick_link("网络学堂", "门户",
                                         "https://x.example.com").ok

    def test_update_moves_group(self, ctl):
        """改到未分组: group 置空, 条目位置不变."""
        ctl.add_quick_link("学堂", "https://learn.example.com", "AI工具")
        ctl.add_quick_link("门户", "https://info.example.com")
        result = ctl.update_quick_link("学堂", "学堂",
                                       "https://learn.example.com")
        assert result.ok
        links = ctl.list_quick_links()
        assert [link.name for link in links] == ["学堂", "门户"]  # 原地更新
        assert links[0].group == ""

    def test_update_missing(self, ctl):
        """定位不到条目返回失败."""
        assert not ctl.update_quick_link("不存在", "x",
                                         "https://x.example.com").ok

    def test_remove(self, ctl):
        """删除写回并留痕; 不存在返回失败."""
        ctl.add_quick_link("学堂", "https://learn.example.com")
        result = ctl.remove_quick_link("学堂")
        assert result.ok
        assert not ctl.list_quick_links()
        ops = ctl.storage.list_operations()
        assert ops[0]["action"] == "remove_quick_link"
        assert ops[0]["path_snapshot"] == "学堂"
        assert not ctl.remove_quick_link("学堂").ok
