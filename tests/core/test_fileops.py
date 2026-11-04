"""fileops.py 单元测试: 文件操作纯逻辑(复制/移动/重命名/新建/防护)."""

import os

import pytest

from shuimu.core.fileops import (
    check_target,
    copy_batch,
    dedupe_path,
    is_within,
    make_dir,
    move_batch,
    rename_entry,
)


class TestDedupePath:
    """重名去重."""

    def test_no_conflict(self, tmp_path):
        """无冲突原样返回."""
        dst = str(tmp_path / "a.txt")
        assert dedupe_path(dst) == dst

    def test_disk_conflict(self, tmp_path):
        """磁盘已存在加 _1, 扩展名保留."""
        (tmp_path / "a.pdf").write_text("x")
        assert dedupe_path(str(tmp_path / "a.pdf")) == str(tmp_path / "a_1.pdf")

    def test_taken_conflict(self, tmp_path):
        """计划内 taken 集合与磁盘同时占用时递增到 _2."""
        (tmp_path / "a_1.txt").write_text("x")
        taken = {str(tmp_path / "a.txt")}
        assert dedupe_path(str(tmp_path / "a.txt"), taken) == str(tmp_path / "a_2.txt")

    def test_taken_none(self, tmp_path):
        """taken 缺省为空集合, 不报错."""
        assert dedupe_path(str(tmp_path / "b.txt")) == str(tmp_path / "b.txt")


class TestIsWithin:
    """路径包含判断."""

    def test_basic(self, tmp_path):
        """子目录在内, 自身在内, 兄弟不在内."""
        sub = tmp_path / "sub"
        sub.mkdir()
        assert is_within(str(sub), str(tmp_path))
        assert is_within(str(tmp_path), str(tmp_path))
        other = tmp_path / "other"
        other.mkdir()
        assert not is_within(str(other), str(sub))

    def test_realpath(self, tmp_path):
        """符号链接先归一再比较."""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(real, link)
        except OSError:
            pytest.skip("环境不支持符号链接")
        assert is_within(str(real / "x"), str(link))


class TestCheckTarget:
    """单条防护."""

    def test_ok(self, tmp_path):
        """源在目标目录在, 目录也不进自身, 返回 None."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        dst = tmp_path / "dst"
        dst.mkdir()
        assert check_target(str(f), str(dst)) is None
        assert check_target(str(dst), str(tmp_path)) is None

    def test_missing_source(self, tmp_path):
        """源不存在给文案."""
        assert check_target(str(tmp_path / "nope"), str(tmp_path)) is not None

    def test_missing_dst(self, tmp_path):
        """目标目录不存在给文案."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert check_target(str(f), str(tmp_path / "nope")) is not None

    def test_dir_into_itself(self, tmp_path):
        """目录复制/移动进自身内部给文案."""
        d = tmp_path / "d"
        (d / "inner").mkdir(parents=True)
        assert check_target(str(d), str(d / "inner")) is not None


class TestCopyBatch:
    """复制批."""

    def test_copy_file_preserves_mtime(self, tmp_path):
        """文件复制内容与 mtime 一致(copy2)."""
        src = tmp_path / "a.txt"
        src.write_text("hello")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        items = copy_batch([str(src)], str(dst_dir))
        copied = dst_dir / "a.txt"
        assert items[0].status == "ok"
        assert copied.read_text() == "hello"
        assert os.stat(copied).st_mtime == os.stat(src).st_mtime

    def test_copy_dir_recursive(self, tmp_path):
        """目录递归复制."""
        src = tmp_path / "d"
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "x.txt").write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        items = copy_batch([str(src)], str(dst_dir))
        assert items[0].status == "ok"
        assert (dst_dir / "d" / "sub" / "x.txt").read_text() == "x"

    def test_conflict_gets_suffix(self, tmp_path):
        """目标重名自动 _1, 原文件不动."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        for name, text in (("a.txt", "old"), ("b.txt", "new")):
            (src_dir / name).write_text(text)
            (dst_dir / name).write_text(text)
        items = copy_batch([str(src_dir / "a.txt"), str(src_dir / "b.txt")],
                           str(dst_dir))
        assert (dst_dir / "a_1.txt").read_text() == "old"
        assert (dst_dir / "b.txt").read_text() == "new"
        assert [i.status for i in items] == ["ok", "ok"]
        assert items[0].dst == str(dst_dir / "a_1.txt")

    def test_same_name_from_different_sources(self, tmp_path):
        """不同源目录同名文件, taken 跨条目累积, 第二个得 _1."""
        s1, s2 = tmp_path / "s1", tmp_path / "s2"
        s1.mkdir()
        s2.mkdir()
        (s1 / "a.txt").write_text("1")
        (s2 / "a.txt").write_text("2")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        items = copy_batch([str(s1 / "a.txt"), str(s2 / "a.txt")], str(dst_dir))
        assert (dst_dir / "a.txt").read_text() == "1"
        assert (dst_dir / "a_1.txt").read_text() == "2"
        assert all(i.status == "ok" for i in items)

    def test_missing_source_single_failure(self, tmp_path):
        """源缺失单条 failed, 其余继续."""
        good = tmp_path / "g.txt"
        good.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        items = copy_batch([str(tmp_path / "nope"), str(good)], str(dst_dir))
        assert items[0].status == "failed"
        assert items[0].error
        assert items[1].status == "ok"
        assert (dst_dir / "g.txt").exists()

    def test_same_dir_copy_skipped(self, tmp_path):
        """复制到源所在目录标记 skipped, 不产生副本."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        items = copy_batch([str(f)], str(tmp_path))
        assert items[0].status == "skipped"
        assert items[0].dst == str(f)
        assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]


class TestMoveBatch:
    """移动批."""

    def test_move(self, tmp_path):
        """移动后原位消失."""
        src = tmp_path / "a.txt"
        src.write_text("x")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        items = move_batch([str(src)], str(dst_dir))
        assert items[0].status == "ok"
        assert not src.exists()
        assert (dst_dir / "a.txt").exists()

    def test_move_conflict(self, tmp_path):
        """目标重名自动 _1."""
        src = tmp_path / "a.txt"
        src.write_text("new")
        dst_dir = tmp_path / "dst"
        dst_dir.mkdir()
        (dst_dir / "a.txt").write_text("old")
        items = move_batch([str(src)], str(dst_dir))
        assert items[0].dst == str(dst_dir / "a_1.txt")
        assert (dst_dir / "a_1.txt").read_text() == "new"

    def test_dir_into_itself_fails(self, tmp_path):
        """目录移入自身内部失败且原位不动."""
        d = tmp_path / "d"
        (d / "inner").mkdir(parents=True)
        (d / "inner" / "x.txt").write_text("x")
        items = move_batch([str(d)], str(d / "inner"))
        assert items[0].status == "failed"
        assert (d / "inner" / "x.txt").exists()

    def test_same_dir_move_skipped(self, tmp_path):
        """移动到源所在目录标记 skipped."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        items = move_batch([str(f)], str(tmp_path))
        assert items[0].status == "skipped"
        assert f.exists()


class TestRenameEntry:
    """重命名."""

    def test_rename_file_and_dir(self, tmp_path):
        """文件与目录改名返回新路径."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert rename_entry(str(f), "b.txt") == str(tmp_path / "b.txt")
        assert (tmp_path / "b.txt").exists()
        d = tmp_path / "d1"
        d.mkdir()
        assert rename_entry(str(d), "d2") == str(tmp_path / "d2")

    def test_rename_strips(self, tmp_path):
        """名称自动去首尾空白."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert rename_entry(str(f), "  b.txt  ") == str(tmp_path / "b.txt")

    @pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", ".", "..", "a.txt"])
    def test_rename_invalid(self, tmp_path, bad):
        """空名/含分隔符/./../同名 -> ValueError."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        with pytest.raises(ValueError):
            rename_entry(str(f), bad)

    def test_rename_target_exists(self, tmp_path):
        """目标已存在 -> ValueError(不做自动去重)."""
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "b.txt").write_text("y")
        with pytest.raises(ValueError):
            rename_entry(str(tmp_path / "a.txt"), "b.txt")

    def test_rename_missing_source(self, tmp_path):
        """源不存在 -> OSError."""
        with pytest.raises(OSError):
            rename_entry(str(tmp_path / "nope"), "b.txt")


class TestMakeDir:
    """新建文件夹."""

    def test_default_name(self, tmp_path):
        """缺省名"新建文件夹"."""
        assert make_dir(str(tmp_path)) == str(tmp_path / "新建文件夹")

    def test_dedupe(self, tmp_path):
        """重名自动 _1."""
        make_dir(str(tmp_path))
        assert make_dir(str(tmp_path)) == str(tmp_path / "新建文件夹_1")

    def test_bad_parent(self, tmp_path):
        """parent 非目录 -> NotADirectoryError."""
        with pytest.raises(NotADirectoryError):
            make_dir(str(tmp_path / "nope"))

    def test_bad_name(self, tmp_path):
        """名称含分隔符 -> ValueError."""
        with pytest.raises(ValueError):
            make_dir(str(tmp_path), "a/b")
