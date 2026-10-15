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
        assert code == 0 and "已移动 1" in out
        assert (target / "文档" / "a.pdf").exists()
        _, log = run(capsys, db, "log")
        assert "organize" in log
