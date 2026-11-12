"""cli.py 单元测试: 子命令行为与退出码."""

import importlib
import json
import os
from copy import deepcopy

import pytest

from shuimu.cli import main
from shuimu.core.campus import CHECK_URL, ROAM_FORM_URL
from shuimu.core.learn import (
    LearnCourse,
    LearnFile,
    LearnHomework,
    LearnNotice,
    download_course_file_url,
)
from shuimu.core.campus_net import MemoryTransport
from shuimu.core.config import DEFAULTS
from shuimu.core.storage import Storage
from tests.core.test_campus import (
    CALLBACK_URL,
    LOGIN_PAGE,
    add_login_prelude,
    add_roam_info_tail,
    success_page,
)
from tests.core.test_learn import (
    SEMESTER,
    WLKCID,
    add_csrf_route,
    add_download_route,
)
from tests.core.test_learn_sync import (
    COURSE_A,
    add_course_detail_routes,
    add_refresh_prelude,
)

# shuimu/cli/__init__.py 把 main 重导出为函数, 遮蔽了同名子模块属性;
# 经 importlib 直取模块对象以访问 _completions 等实现.
cli_main = importlib.import_module("shuimu.cli.main")

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


def run_full(capsys, db, *argv):
    """执行 CLI 并返回 (退出码, stdout, stderr)."""
    code = main(["--db", db, *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


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

    def test_import_failure_writes_stderr(self, capsys, db, sched):
        """失败消息走 stderr 且 stdout 为空(契约: 成功 stdout / 失败 stderr)."""
        code = main(["--db", db, "--schedule", sched,
                     "import-schedule", "/nonexistent.html"])
        captured = capsys.readouterr()
        assert code == 2
        assert "导入失败" in captured.err
        assert captured.out == ""

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


@pytest.fixture()
def links_yaml(tmp_path, monkeypatch):
    """独立的常用链接 YAML 路径(模板指向不存在的文件, 隔离仓库模板)."""
    monkeypatch.setattr("shuimu.core.controller.template_links_path",
                        lambda: str(tmp_path / "no-tpl.yaml"))
    return str(tmp_path / "links.yaml")


def run_links(capsys, db, links_yaml, *argv):
    """执行带 --links 隔离路径的 CLI 并返回 (退出码, stdout, stderr)."""
    code = main(["--db", db, "--links", links_yaml, *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


class TestLinksCommands:
    """常用链接子命令(与 GUI 同源)."""

    def test_add_list_grouped(self, capsys, db, links_yaml):
        """添加(带分组与不带分组)后按分组列出, 未分组末尾."""
        run_links(capsys, db, links_yaml,
                  "add-link", "学堂", "https://learn.example.com",
                  "--group", "课堂资源")
        run_links(capsys, db, links_yaml,
                  "add-link", "散链", "https://misc.example.com")
        code, out, _ = run_links(capsys, db, links_yaml, "links")
        assert code == 0
        assert "== 课堂资源 ==" in out and "学堂" in out
        assert out.rindex("未分组") > out.rindex("课堂资源")

    def test_add_rejects_bad_url(self, capsys, db, links_yaml):
        """非 http 前缀退出码 2, 不落盘."""
        code, _, err = run_links(capsys, db, links_yaml,
                                 "add-link", "坏", "x.example.com")
        assert code == 2 and err.strip()
        code, out, _ = run_links(capsys, db, links_yaml, "links")
        assert "坏" not in out

    def test_edit_link_partial(self, capsys, db, links_yaml):
        """仅改给定字段, 未给字段保持原值."""
        run_links(capsys, db, links_yaml,
                  "add-link", "学堂", "https://learn.example.com",
                  "--group", "课堂资源")
        code, out, _ = run_links(capsys, db, links_yaml,
                                 "edit-link", "学堂", "--group", "AI工具")
        assert code == 0 and "已更新" in out
        _, out, _ = run_links(capsys, db, links_yaml, "links")
        assert "== AI工具 ==" in out and "https://learn.example.com" in out

    def test_edit_link_missing(self, capsys, db, links_yaml):
        """定位不到链接退出码 2."""
        code, _, err = run_links(capsys, db, links_yaml,
                                 "edit-link", "不存在", "--url",
                                 "https://x.example.com")
        assert code == 2 and "不存在" in err

    def test_rm_link(self, capsys, db, links_yaml):
        """删除后列表不再出现."""
        run_links(capsys, db, links_yaml,
                  "add-link", "学堂", "https://learn.example.com")
        code, _, _ = run_links(capsys, db, links_yaml, "rm-link", "学堂")
        assert code == 0
        _, out, _ = run_links(capsys, db, links_yaml, "links")
        assert "学堂" not in out

    def test_open_link_dispatches(self, capsys, db, links_yaml, monkeypatch):
        """open-link 经 URL 分发层默认处理器打开; 不存在退出码 2.

        假掉 open_url_external 本身: main() 会把它注册为默认处理器,
        从而整条链路(注册->分发)都在测试内闭环, 不真开浏览器.
        """
        opened = []
        monkeypatch.setattr("shuimu.core.url_open._state",
                            {"default": None, "registry": {}})
        monkeypatch.setattr(
            "shuimu.core.url_open.open_url_external",
            lambda url: opened.append(url) or True)
        run_links(capsys, db, links_yaml,
                  "add-link", "学堂", "https://learn.example.com")
        code, _, _ = run_links(capsys, db, links_yaml, "open-link", "学堂")
        assert code == 0 and opened == ["https://learn.example.com"]
        code, _, err = run_links(capsys, db, links_yaml, "open-link", "没有")
        assert code == 2 and "没有" in err


class TestReplCompletion:
    """REPL Tab 补全候选(纯逻辑)."""

    OPTIONS = {"add-link": ["--group"], "edit-link": ["--name", "--url",
                                                      "--group"],
               "links": [], "organize": ["--yes"]}

    def test_first_word_subcommands_and_keywords(self):
        """行首词补子命令名与 REPL 关键字, 按前缀过滤且有序."""
        assert cli_main._completions("add-l", self.OPTIONS) == \
            ["add-link"]
        assert cli_main._completions("", self.OPTIONS)[0] == "add-link"
        assert cli_main._completions("e", self.OPTIONS) == \
            ["edit-link", "exit"]
        assert cli_main._completions("help", self.OPTIONS) == ["help"]
        assert cli_main._completions("zzz", self.OPTIONS) == []

    def test_option_completion_for_current_command(self):
        """'-'-开头词补当前子命令的选项."""
        assert cli_main._completions("add-link --", self.OPTIONS) == \
            ["--group"]
        assert cli_main._completions("edit-link --na", self.OPTIONS) == \
            ["--name"]
        assert cli_main._completions("no-such --x", self.OPTIONS) == []

    def test_trailing_space_completes_filenames(self, tmp_path, monkeypatch):
        """子命令后接空格补文件名; 目录补路径分隔符."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "讲义.pdf").write_text("x")
        (tmp_path / "课件").mkdir()
        got = cli_main._completions("add 1 ", self.OPTIONS)
        assert "课件" + os.sep in got and "讲义.pdf" in got
        got = cli_main._completions("organize 讲", self.OPTIONS)
        assert got == ["讲义.pdf"]

    def test_subcommand_options_reflected_from_parser(self):
        """选项清单从 build_parser 反射(含每个子命令自带的 -h/--help)."""
        opts = cli_main._subcommand_options(cli_main.build_parser())
        assert opts["add-link"] == ["-h", "--help", "--group"]
        assert "--series" in opts["rm-event"]
        assert "--name" in opts["edit-link"]
        assert opts["links"] == ["-h", "--help"]


@pytest.fixture()
def plugins_isolated(tmp_path, monkeypatch):
    """插件状态与外部目录隔离到 tmp(内置目录仍用真实仓库插件)."""
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(tmp_path / "ext-plugins"))


@pytest.mark.usefixtures("plugins_isolated")
class TestPluginCommands:
    """插件相关子命令."""

    def test_plugins_list_contains_hwcheck(self, capsys, db):
        """plugins 列出内置 hwcheck 且状态可用."""
        code, out = run(capsys, db, "plugins")
        assert code == 0
        assert "hwcheck" in out and "作业提交自查" in out and "内置" in out

    def test_plugin_run_ok(self, capsys, db, tmp_path):
        """合规目录运行退出 0, 输出含检查摘要."""
        (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")
        code, out = run(capsys, db, "plugin-run", "hwcheck",
                        "--path", str(tmp_path))
        assert code == 0
        assert "作业提交自查" in out and "检查" in out

    def test_plugin_run_findings_exit_2(self, capsys, db, tmp_path):
        """缺必交文件退出 2, 报告行含错误标记."""
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        code, out, _err = run_full(capsys, db, "plugin-run", "hwcheck",
                                   "--path", str(tmp_path))
        assert code == 2
        assert "缺少必交文件" in out

    def test_plugin_run_unknown_plugin(self, capsys, db, tmp_path):
        """未知插件退出 2."""
        code, _out, err = run_full(capsys, db, "plugin-run", "ghost",
                                   "--path", str(tmp_path))
        assert code == 2 and "不存在" in err

    def test_plugin_run_bad_set_form(self, capsys, db, tmp_path):
        """--set 缺 = 退出 2."""
        code, _out, err = run_full(capsys, db, "plugin-run", "hwcheck",
                                   "--path", str(tmp_path), "--set", "novalue")
        assert code == 2 and "K=V" in err

    def test_disable_enable_roundtrip(self, capsys, db, tmp_path):
        """disable/enable 往返, 留痕含 plugin_disable."""
        assert run(capsys, db, "plugin-disable", "hwcheck")[0] == 0
        code, out = run(capsys, db, "plugins")
        assert "已禁用" in out
        (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4")
        code, _out, err = run_full(capsys, db, "plugin-run", "hwcheck",
                                   "--path", str(tmp_path))
        assert code == 2 and "已禁用" in err
        assert run(capsys, db, "plugin-enable", "hwcheck")[0] == 0
        assert run(capsys, db, "plugin-run", "hwcheck",
                   "--path", str(tmp_path))[0] == 0
        code, out = run(capsys, db, "log")
        assert "plugin_disable" in out and "plugin_enable" in out

    def test_plugin_repl_completion(self):
        """REPL 补全含 plugin-run(经 parser 反射自动生效)."""
        opts = cli_main._subcommand_options(cli_main.build_parser())
        assert cli_main._completions("plugin-", opts) == \
            sorted(["plugin-run", "plugin-enable", "plugin-disable"])


@pytest.fixture()
def network_plugin(tmp_path, monkeypatch):
    """隔离目录 + 一个外部 network 插件."""
    ext = tmp_path / "ext"
    (ext / "nettool").mkdir(parents=True)
    (ext / "nettool" / "manifest.yaml").write_text(
        "id: nettool\nname: 网络工具\nversion: '1'\n"
        "permissions: [network]\napplies_to: [app]\n", encoding="utf-8")
    (ext / "nettool" / "plugin.py").write_text(
        "from shuimu.core.pluginapi import make_report\n"
        "def run(job):\n    return make_report([], summary='ok')\n",
        encoding="utf-8")
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(ext))


@pytest.mark.usefixtures("network_plugin")
class TestNetworkPluginEnable:
    """network 插件启用确认流."""

    def test_listed_disabled_by_default(self, capsys, db):
        """默认禁用且原因可见."""
        code, out = run(capsys, db, "plugins")
        assert code == 0
        assert "nettool" in out and "已禁用" in out and "默认禁用" in out

    def test_enable_denied_then_confirmed(self, capsys, db, monkeypatch):
        """拒绝确认退出 2 且未启用; 输 y 启用成功."""
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        code, _out, err = run_full(capsys, db, "plugin-enable", "nettool")
        assert code == 2 and "已取消" in err
        code, out = run(capsys, db, "plugins")
        assert "已禁用" in out
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        code, out = run(capsys, db, "plugin-enable", "nettool")
        assert code == 0 and "已启用" in out
        code, out = run(capsys, db, "plugins")
        assert "可用" in out

    def test_enable_yes_skips_confirm(self, capsys, db):
        """--yes 跳过交互直接启用."""
        code, out = run(capsys, db, "plugin-enable", "nettool", "--yes")
        assert code == 0 and "已启用" in out

    def test_run_disabled_network_plugin(self, capsys, db):
        """禁用状态下运行被拒绝."""
        code, _out, err = run_full(capsys, db, "plugin-run", "nettool")
        assert code == 2 and "已禁用" in err


@pytest.fixture()
def campus_cli(tmp_path, monkeypatch):
    """校园 CLI 隔离: 插件状态/外部目录/会话/指纹路径与传输层."""
    transport = MemoryTransport()
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(tmp_path / "ext-plugins"))
    monkeypatch.setattr("shuimu.core.controller.default_campus_session_path",
                        lambda: str(tmp_path / "campus-session.json"))
    monkeypatch.setattr(
        "shuimu.core.controller.default_campus_fingerprint_path",
        lambda: str(tmp_path / "campus-fingerprint"))
    monkeypatch.setattr("shuimu.core.controller.UrllibTransport",
                        lambda: transport)
    return transport


class TestCampusCommands:
    """校园账户子命令."""

    @pytest.mark.usefixtures("campus_cli")
    def test_status_not_logged_in(self, capsys, db):
        """无会话: campus-status --no-check 退出 2."""
        code, _out, err = run_full(capsys, db, "campus-status",
                                   "--no-check")
        assert code == 2 and "未登录" in err

    @pytest.mark.usefixtures("campus_cli")
    def test_logout_without_session(self, capsys, db):
        """本无会话登出成功."""
        code, out = run(capsys, db, "campus-logout")
        assert code == 0 and "本无会话" in out

    def test_login_requires_enable(self, capsys, db, campus_cli,
                                   monkeypatch):
        """未启用校园插件: 登录被拒且零请求."""
        monkeypatch.setattr("getpass.getpass", lambda _prompt: "pw")
        code, _out, err = run_full(capsys, db, "campus-login", "20231234567")
        assert code == 2 and "未启用" in err
        assert not campus_cli.requests

    def test_full_roundtrip(self, capsys, db, campus_cli, monkeypatch):
        """启用 -> 登录 -> 只读状态 -> 登出全链路."""
        assert run(capsys, db, "plugin-enable", "campus", "--yes")[0] == 0
        add_login_prelude(campus_cli)
        campus_cli.add("POST", CHECK_URL, text=success_page(CALLBACK_URL))
        campus_cli.add("GET", CALLBACK_URL, text="webvpn ok")
        campus_cli.add("GET", ROAM_FORM_URL, text=LOGIN_PAGE)
        campus_cli.add("POST", CHECK_URL, text=success_page(
            "https://info.tsinghua.edu.cn/f/info/gxfw_fg/common/index"))
        add_roam_info_tail(campus_cli)
        monkeypatch.setattr("getpass.getpass", lambda _prompt: "pw-secret")
        code, out = run(capsys, db, "campus-login", "20231234567")
        assert code == 0 and "已登录" in out and "张三" in out
        code, out = run(capsys, db, "campus-status", "--no-check")
        assert code == 0 and "张三" in out
        code, out = run(capsys, db, "campus-logout")
        assert code == 0 and "已登出" in out
        code, out = run(capsys, db, "log")
        assert "campus_login" in out and "pw-secret" not in out


@pytest.fixture()
def learn_cli(tmp_path, monkeypatch):
    """学堂 CLI 隔离: 插件状态/外部目录/会话/指纹路径与传输层, 预写会话."""
    transport = MemoryTransport()
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(tmp_path / "ext-plugins"))
    session = str(tmp_path / "campus-session.json")
    monkeypatch.setattr("shuimu.core.controller.default_campus_session_path",
                        lambda: session)
    monkeypatch.setattr(
        "shuimu.core.controller.default_campus_fingerprint_path",
        lambda: str(tmp_path / "campus-fingerprint"))
    monkeypatch.setattr("shuimu.core.controller.UrllibTransport",
                        lambda: transport)
    session_data = {"version": 1, "username": "20231234567",
                    "cookies": {"webvpn.tsinghua.edu.cn":
                                {"wengine_vpn_ticket": "t1"}},
                    "profile": {}, "checked_at": 0, "saved_at": 0}
    with open(session, "w", encoding="utf-8") as f:
        json.dump(session_data, f)
    return transport


def seed_learn_cli(db_path):
    """预置学堂缓存(课/作业三态/课件/公告), 返回本地课程 id."""
    storage = Storage(db_path)
    try:
        local = storage.add_course("AI 编程")
        storage.learn_sync_courses(SEMESTER, [LearnCourse(**COURSE_A)])
        storage.learn_sync_homework([
            LearnHomework(wlkcid=COURSE_A["wlkcid"], zyid="Z1",
                          state="pending", bt="作业一",
                          jzsj="1794758340000"),
            LearnHomework(wlkcid=COURSE_A["wlkcid"], zyid="Z2",
                          state="graded", bt="作业二", cj="95"),
        ])
        storage.learn_sync_files([
            LearnFile(wlkcid=COURSE_A["wlkcid"], kjxxid="K1", wjid="WJ1",
                      bt="讲义.pdf", wjdx=2048, kjflid="C1")],
            {"C1": "电子教案"})
        storage.learn_sync_notices([
            LearnNotice(wlkcid=COURSE_A["wlkcid"], ggid="G1", bt="公告一",
                        fbsj="2026-10-16 16:46", sfyd=True)])
        storage.learn_set_course_link(COURSE_A["wlkcid"], local)
        return local
    finally:
        storage.close()


def enable_learn(capsys, db):
    """启用 learn 插件(--yes 跳过确认)."""
    code, out = run(capsys, db, "plugin-enable", "learn", "--yes")
    assert code == 0 and "已启用" in out


class TestLearnCommands:
    """网络学堂子命令."""

    def test_status_empty(self, capsys, db):
        """空缓存概况, 退出 0."""
        code, out = run(capsys, db, "learn-status")
        assert code == 0 and "0 课" in out

    def test_refresh_requires_enable(self, capsys, db, learn_cli):
        """未启用: 退出 2 且零请求."""
        code, _out, err = run_full(capsys, db, "learn-refresh")
        assert code == 2 and "未启用" in err
        assert not learn_cli.requests

    def test_refresh_ok_then_courses(self, capsys, db, learn_cli):
        """启用后刷新成功, learn-courses 显示课名与关联."""
        enable_learn(capsys, db)
        code, _ = run(capsys, db, "add-course", "AI 编程")   # 供自动匹配
        assert code == 0
        add_refresh_prelude(learn_cli)
        add_course_detail_routes(learn_cli, WLKCID,
                                 homework=[{"zyid": "Z1", "bt": "作业一"}])
        add_course_detail_routes(learn_cli, "2025-2026-1-00000002")
        code, out = run(capsys, db, "learn-refresh")
        assert code == 0 and "新作业 1" in out
        code, out = run(capsys, db, "learn-courses")
        assert code == 0 and "AI 编程" in out and "-> AI 编程" in out

    def test_homework_grouping(self, capsys, db):
        """三态分组与截止日期."""
        seed_learn_cli(db)
        code, out = run(capsys, db, "learn-homework")
        assert code == 0
        assert "[未交] 1" in out and "[已批] 1" in out
        assert "作业一" in out and "作业二" in out
        code, out = run(capsys, db, "learn-homework", WLKCID)
        assert code == 0 and "[未交] 1" in out

    def test_files_and_notices(self, capsys, db):
        """课件与公告列表."""
        seed_learn_cli(db)
        code, out = run(capsys, db, "learn-files", WLKCID)
        assert code == 0 and "讲义.pdf" in out and "电子教案" in out
        assert "2 KB" in out
        code, out = run(capsys, db, "learn-notices", WLKCID)
        assert code == 0 and "公告一" in out and "已读" in out

    def test_download_ok(self, capsys, db, learn_cli, tmp_path):
        """下载落盘并登记(未关联的课程文件仍落盘)."""
        enable_learn(capsys, db)
        seed_learn_cli(db)
        add_csrf_route(learn_cli)
        add_download_route(learn_cli, download_course_file_url("WJ1"),
                           b"pdf-bytes")
        out_dir = str(tmp_path / "out")
        code, out = run(capsys, db, "learn-download", "--id", "WJ1",
                        "--out", out_dir)
        assert code == 0 and "已下载" in out
        path = os.path.join(out_dir, "AI 编程", "讲义.pdf")
        assert os.path.exists(path)
        storage = Storage(db)
        try:
            local = next(c["id"] for c in storage.list_courses()
                         if c["name"] == "AI 编程")
            assert any(r["path_or_url"] == path
                       for r in storage.list_resources(local))
        finally:
            storage.close()
