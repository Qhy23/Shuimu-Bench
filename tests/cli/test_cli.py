"""cli.py 单元测试: 子命令行为与退出码."""

import os
from copy import deepcopy

import pytest

from shuimu.cli import main
from shuimu.core.config import DEFAULTS

CAL_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                           "portal_calendar_sample.html")


@pytest.fixture()
def db(tmp_path):
    """每个测试用独立数据库."""
    return str(tmp_path / "cli.db")


@pytest.fixture()
def sched(tmp_path, monkeypatch):
    """独立的日程 YAML 路径(模板指向空文件, 隔离仓库模板)."""
    tpl = tmp_path / "tpl.yaml"
    tpl.write_text("todos: []\n", encoding="utf-8")
    monkeypatch.setattr("shuimu.core.controller.template_schedule_path",
                        lambda: str(tpl))
    return str(tmp_path / "schedule.yaml")


def run(capsys, db, *argv, schedule=None):
    """执行 CLI 并返回 (退出码, stdout)."""
    args = ["--db", db]
    if schedule:
        args += ["--schedule", schedule]
    code = main([*args, *argv])
    return code, capsys.readouterr().out


@pytest.fixture()
def semester(monkeypatch):
    """固定学期配置(第 1 周 = 2026-09-14, 共 18 周)供周数展开测试."""
    cfg = deepcopy(DEFAULTS)
    cfg["schedule"]["semester_start"] = "2026-09-14"
    cfg["schedule"]["week_count"] = 18
    monkeypatch.setattr("shuimu.core.controller.load_config", lambda: cfg)


class TestCourseCommands:
    """课程相关子命令."""

    def test_add_and_list(self, capsys, db):
        """建课后能在列表中看到."""
        code, out = run(capsys, db, "add-course", "高等数学")
        assert code == 0 and "高等数学" in out
        code, out = run(capsys, db, "courses")
        assert code == 0 and "高等数学" in out


class TestResourceCommands:
    """资源登记与移除."""

    def test_add_file_and_link(self, capsys, db, tmp_path):
        """文件与链接均可登记, 类型自动判断."""
        run(capsys, db, "add-course", "物理")
        f = tmp_path / "a.pdf"
        f.write_text("x")
        code, out = run(capsys, db, "add", "1", str(f))
        assert code == 0 and "[文件]" in out
        code, out = run(capsys, db, "add", "1", "https://learn.tsinghua.edu.cn")
        assert code == 0 and "[链接]" in out
        code, out = run(capsys, db, "resources", "1")
        assert "a.pdf" in out and "learn.tsinghua" in out

    def test_add_missing_path_rejected(self, capsys, db):
        """不存在的路径返回退出码 2."""
        run(capsys, db, "add-course", "物理")
        code, _ = run(capsys, db, "add", "1", "/nonexistent/xyz")
        assert code == 2

    def test_remove(self, capsys, db, tmp_path):
        """按 id 移除后列表中不再出现."""
        run(capsys, db, "add-course", "物理")
        f = tmp_path / "a.txt"
        f.write_text("x")
        run(capsys, db, "add", "1", str(f))
        code, _ = run(capsys, db, "remove", "1")
        assert code == 0
        _, out = run(capsys, db, "resources", "1")
        assert "a.txt" not in out
        assert f.exists()  # 磁盘文件不动


class TestSearchCommand:
    """search 子命令."""

    def test_search_by_name_and_path(self, capsys, db, tmp_path):
        """按显示名与路径子串命中, 输出含课程名/类型/完整路径(含空格)."""
        run(capsys, db, "add-course", "高等数学")
        f1 = tmp_path / "machine_learning.pdf"
        f1.write_text("x")
        run(capsys, db, "add", "2", str(f1))
        d = tmp_path / "课件 2026"
        d.mkdir()
        f2 = d / "lecture notes.pdf"
        f2.write_text("x")
        run(capsys, db, "add", "1", str(f2))  # #1 为默认"未分组"
        code, out = run(capsys, db, "search", "learning")
        assert code == 0 and "[文件]" in out and "高等数学" in out
        code, out = run(capsys, db, "search", "课件 2026")
        assert code == 0 and "lecture notes.pdf" in out and "未分组" in out
        assert str(f2) in out  # 路径含空格完整显示

    def test_search_case_insensitive(self, capsys, db, tmp_path):
        """英文关键词大小写不敏感."""
        f = tmp_path / "Report.PDF"
        f.write_text("x")
        run(capsys, db, "add", "1", str(f))
        code, out = run(capsys, db, "search", "report.pdf")
        assert code == 0 and "Report.PDF" in out

    def test_search_wildcards_literal(self, capsys, db, tmp_path):
        """% 与 _ 按字面匹配, 不当通配符."""
        for name in ("100%_done.txt", "100x_done.txt",
                     "my notes.txt", "my_notes.txt"):
            f = tmp_path / name
            f.write_text("x")
            run(capsys, db, "add", "1", str(f))
        code, out = run(capsys, db, "search", "100%")
        assert code == 0 and "100%_done.txt" in out
        assert "100x_done.txt" not in out
        code, out = run(capsys, db, "search", "my_notes")
        assert code == 0 and "my_notes.txt" in out
        assert "my notes.txt" not in out

    def test_search_link_and_stale(self, capsys, db, tmp_path):
        """链接与失效资源可被搜到, 状态分别为 ok 与失效."""
        run(capsys, db, "add", "1", "https://learn.tsinghua.edu.cn/wiki")
        f = tmp_path / "stale.txt"
        f.write_text("x")
        run(capsys, db, "add", "1", str(f))
        f.unlink()
        code, out = run(capsys, db, "search", "tsinghua")
        assert code == 0 and "[链接]" in out and "learn.tsinghua.edu.cn" in out
        code, out = run(capsys, db, "search", "stale")
        assert code == 0 and "失效" in out and str(f) in out

    def test_search_no_results(self, capsys, db):
        """无结果给友好提示, 退出码 0."""
        code, out = run(capsys, db, "search", "不存在的关键词")
        assert code == 0 and "没有匹配" in out

    def test_search_empty_keyword_rejected(self, capsys, db):
        """空关键词报错到 stderr, 退出码 2."""
        code = main(["--db", db, "search", ""])
        captured = capsys.readouterr()
        assert code == 2 and "关键词不能为空" in captured.err

    def test_search_missing_keyword_rejected(self, db):
        """缺关键词参数走 argparse 报错, 退出码 2(非 traceback)."""
        with pytest.raises(SystemExit) as exc:
            main(["--db", db, "search"])
        assert exc.value.code == 2


class TestDeleteCourseCommand:
    """del-course 子命令."""

    def test_delete_course(self, capsys, db, tmp_path):
        """删除课程并级联移除其资源, 留痕可查."""
        run(capsys, db, "add-course", "临时课")  # #1 是自动建的"未分组", 临时课为 #2
        f = tmp_path / "a.txt"
        f.write_text("x")
        run(capsys, db, "add", "2", str(f))
        code, out = run(capsys, db, "del-course", "2")
        assert code == 0 and "级联移除 1" in out
        _, out = run(capsys, db, "courses")
        assert "临时课" not in out
        assert "未分组" in out  # 默认课程不受影响
        _, log = run(capsys, db, "log")
        assert "delete_course" in log
        assert f.exists()

    def test_delete_missing_course_rejected(self, capsys, db):
        """删除不存在的课程返回退出码 2."""
        code, _ = run(capsys, db, "del-course", "999")
        assert code == 2


class TestRepl:
    """交互模式: 不带子命令启动, 逐行输入."""

    def test_repl_session(self, capsys, db, monkeypatch):
        """REPL 内连续执行多条命令后 exit 退出."""
        inputs = iter(["add-course 数学", "courses", "bad-cmd", "exit"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))
        code = main(["--db", db])
        out = capsys.readouterr().out
        assert code == 0
        assert "已建课程" in out
        assert "数学" in out  # courses 输出
        # bad-cmd 不应中断 REPL(argparse 错误后继续)

    def test_repl_eof_exits(self, db, monkeypatch):
        """EOF(Ctrl+D)正常退出."""
        def raise_eof(_):
            raise EOFError
        monkeypatch.setattr("builtins.input", raise_eof)
        assert main(["--db", db]) == 0


class TestOrganizeCommand:
    """整理子命令: 预览不动作, --yes 才执行."""

    def test_preview_does_not_move(self, capsys, db, tmp_path):
        """默认只打印计划, 文件原地不动."""
        target = tmp_path / "messy"
        target.mkdir()
        (target / "a.pdf").write_text("x")
        code, out = run(capsys, db, "organize", str(target))
        assert code == 0 and "预览" in out
        assert (target / "a.pdf").exists()

    def test_execute_with_yes(self, capsys, db, tmp_path):
        """--yes 执行移动并留痕."""
        target = tmp_path / "messy"
        target.mkdir()
        (target / "a.pdf").write_text("x")
        code, out = run(capsys, db, "organize", str(target), "--yes")
        assert code == 0 and "整理完成" in out and "移动 1" in out
        assert (target / "文档" / "a.pdf").exists()
        _, log = run(capsys, db, "log")
        assert "organize" in log


class TestScheduleCommands:
    """日程子命令(与 GUI 同源动作)."""

    def test_add_todo_and_list(self, capsys, db, sched):
        """新建待办后列表可见, 逾期标注."""
        code, out = run(capsys, db, "add-todo", "微积分作业",
                        "--due", "2026-10-26", "--course", "微积分", schedule=sched)
        assert code == 0 and "已添加待办" in out
        code, out = run(capsys, db, "todos", schedule=sched)
        assert code == 0 and "微积分作业" in out and "微积分" in out

    def test_toggle_todo(self, capsys, db, sched):
        """翻转完成状态."""
        run(capsys, db, "add-todo", "任务", schedule=sched)
        code, out = run(capsys, db, "toggle-todo", "1", schedule=sched)
        assert code == 0 and "完成" in out
        code, out = run(capsys, db, "todos", schedule=sched)
        assert "[x]" in out

    def test_import_schedule(self, capsys, db, sched):
        """导入个人校历 html, 联排合并提示可见."""
        code, out = run(capsys, db, "import-schedule", CAL_FIXTURE,
                        schedule=sched)
        assert code == 0 and "已导入课程 3 条" in out and "联排合并 1 处" in out
        code, out = run(capsys, db, "schedule", "--date", "2026-10-26",
                        schedule=sched)
        assert "高等数学A(1)" in out and "AI 编程进阶" in out
        assert "#1" in out  # 全局序号

    @pytest.mark.usefixtures("semester")
    def test_schedule_week_and_empty(self, capsys, db, sched):
        """--week 按学期周显示, 空周提示."""
        run(capsys, db, "add-event", "答疑", "--weekday", "3",
            "--session", "1", "--weeks", "2", schedule=sched)
        code, out = run(capsys, db, "schedule", "--week", "2", schedule=sched)
        assert code == 0 and "第 2 周" in out and "答疑" in out
        code, out = run(capsys, db, "schedule", "--week", "9", schedule=sched)
        assert "本周无日程" in out

    def test_add_event_date_mode(self, capsys, db, sched):
        """按日期模式添加单次日程."""
        code, out = run(capsys, db, "add-event", "组会",
                        "--date", "2026-10-28", "--start", "14:00",
                        "--end", "16:00", "--location", "实验室", schedule=sched)
        assert code == 0 and "已添加手动日程 1 条" in out
        code, out = run(capsys, db, "schedule", "--date", "2026-10-28",
                        schedule=sched)
        assert "组会" in out and "[手动]" in out

    @pytest.mark.usefixtures("semester")
    def test_add_event_week_mode_series(self, capsys, db, sched):
        """按周数模式展开为系列并共享系列号."""
        code, out = run(capsys, db, "add-event", "答疑", "--weekday", "3",
                        "--session", "1", "--weeks", "1-3,5", schedule=sched)
        assert code == 0 and "已添加手动日程 4 条(同系列" in out
        code, out = run(capsys, db, "schedule", "--week", "1", schedule=sched)
        assert "系列#" in out

    def test_add_event_bad_args(self, capsys, db, sched):
        """缺起止/结束早于开始/未选模式均退出码 2."""
        code, _ = run(capsys, db, "add-event", "x", "--date", "2026-10-28",
                      schedule=sched)
        assert code == 2
        code, _ = run(capsys, db, "add-event", "x", "--date", "2026-10-28",
                      "--start", "10:00", "--end", "09:00", schedule=sched)
        assert code == 2
        code, _ = run(capsys, db, "add-event", "x", "--weekday", "2",
                      "--start", "10:00", "--end", "11:00", schedule=sched)
        assert code == 2  # 周数模式缺 --weeks

    @pytest.mark.usefixtures("semester")
    def test_edit_and_remove_event(self, capsys, db, sched):
        """按序号修改与删除, --series 作用于整个系列."""
        run(capsys, db, "add-event", "答疑", "--weekday", "3",
            "--session", "1", "--weeks", "1-3", schedule=sched)
        run(capsys, db, "add-event", "单次", "--date", "2026-10-26",
            "--start", "10:00", "--end", "11:00", schedule=sched)
        # #1-#3 系列, #4 单次; 改单次标题
        code, out = run(capsys, db, "edit-event", "4", "--title", "改名",
                        schedule=sched)
        assert code == 0 and "已修改1 条" in out
        code, out = run(capsys, db, "schedule", "--date", "2026-10-26",
                        schedule=sched)
        assert "改名" in out
        # 整个系列改时间
        code, out = run(capsys, db, "edit-event", "1", "--start",
                        "19:20", "--end", "20:55", "--series", schedule=sched)
        assert code == 0 and "系列 3 场" in out
        # 删除整个系列
        code, out = run(capsys, db, "rm-event", "1", "--series", schedule=sched)
        assert code == 0 and "系列 3 场" in out
        code, out = run(capsys, db, "schedule", "--week", "1", schedule=sched)
        assert "答疑" not in out

    def test_edit_event_bad_index(self, capsys, db, sched):
        """序号越界退出码 2."""
        code, _ = run(capsys, db, "edit-event", "9", "--title", "x",
                      schedule=sched)
        assert code == 2

    def test_schedule_week_without_semester(self, capsys, db, sched,
                                            monkeypatch):
        """未配置学期起点时 --week 报错."""
        cfg = deepcopy(DEFAULTS)
        cfg["schedule"]["semester_start"] = ""
        monkeypatch.setattr("shuimu.core.controller.load_config", lambda: cfg)
        code, _ = run(capsys, db, "schedule", "--week", "3", schedule=sched)
        assert code == 2
