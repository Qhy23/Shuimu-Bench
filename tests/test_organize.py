"""organize.py 单元测试: 归类计划, 冲突去重, 执行."""

import os

import pytest

from shuimu import organize


@pytest.fixture()
def messy_dir(tmp_path):
    """一个待整理的目录: 多种类型 + 隐藏文件 + 子目录."""
    for name in ("a.pdf", "b.png", "c.py", "d.xyz", ".hidden"):
        (tmp_path / name).write_text("x")
    (tmp_path / "subdir").mkdir()
    return str(tmp_path)


class TestCategory:
    """扩展名归类."""

    def test_known_types(self):
        """常见扩展名归入对应类别(大小写不敏感)."""
        assert organize.category_of("课件.PDF") == "文档"
        assert organize.category_of("图.PNG") == "图片"
        assert organize.category_of("main.py") == "代码"

    def test_unknown_fallback(self):
        """未知扩展名与无扩展名归入"其他"."""
        assert organize.category_of("data.xyz") == "其他"
        assert organize.category_of("noext") == "其他"


class TestBuildPlan:
    """计划生成."""

    def test_plan_targets_category_dirs(self, messy_dir):
        """计划把文件移入对应类别子目录."""
        plan = organize.build_plan(messy_dir)
        by_src = {os.path.basename(i.src): i.dst for i in plan}
        assert by_src["a.pdf"].endswith(os.path.join("文档", "a.pdf"))
        assert by_src["b.png"].endswith(os.path.join("图片", "b.png"))
        assert by_src["c.py"].endswith(os.path.join("代码", "c.py"))
        assert by_src["d.xyz"].endswith(os.path.join("其他", "d.xyz"))

    def test_hidden_and_subdir_skipped(self, messy_dir):
        """隐藏文件与子目录不进计划."""
        names = [os.path.basename(i.src) for i in organize.build_plan(messy_dir)]
        assert ".hidden" not in names
        assert "subdir" not in names
        assert len(names) == 4

    def test_conflict_deduped(self, tmp_path):
        """目标已存在时自动加 _1 后缀, 不覆盖已有文件."""
        (tmp_path / "文档").mkdir()
        (tmp_path / "文档" / "a.pdf").write_text("old")
        (tmp_path / "a.pdf").write_text("new")
        plan = organize.build_plan(str(tmp_path))
        assert len(plan) == 1
        assert plan[0].dst.endswith(os.path.join("文档", "a_1.pdf"))

    def test_bad_directory_raises(self):
        """非法目录抛 NotADirectoryError."""
        with pytest.raises(NotADirectoryError):
            organize.build_plan("/nonexistent/path/xyz")


class TestExecutePlan:
    """执行与错误处理."""

    def test_moves_files(self, messy_dir):
        """执行后文件到位, 原位不再存在."""
        plan = organize.build_plan(messy_dir)
        moved, errors = organize.execute_plan(plan)
        assert moved == 4 and not errors
        assert os.path.isfile(os.path.join(messy_dir, "文档", "a.pdf"))
        assert not os.path.isfile(os.path.join(messy_dir, "a.pdf"))

    def test_partial_failure_continues(self, messy_dir):
        """单项失败不中断整体, 错误被收集."""
        plan = organize.build_plan(messy_dir)
        os.remove(plan[0].src)  # 制造单点失败
        moved, errors = organize.execute_plan(plan)
        assert moved == 3
        assert len(errors) == 1
