"""cli.py 单元测试: 子命令行为与退出码."""

import pytest

from shuimu.cli import main


@pytest.fixture()
def db(tmp_path):
    """每个测试用独立数据库."""
    return str(tmp_path / "cli.db")


def run(capsys, db, *argv):
    """执行 CLI 并返回 (退出码, stdout)."""
    code = main(["--db", db, *argv])
    return code, capsys.readouterr().out


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
