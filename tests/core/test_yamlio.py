"""yamlio.py 单元测试: 原子写盘的替换重试与模板播种."""

import os

import pytest
import yaml

from shuimu.core.yamlio import atomic_write_yaml, ensure_from_template


class TestAtomicWrite:
    """原子重写."""

    def test_write_and_roundtrip(self, tmp_path):
        """写入头部与数据, 读回一致."""
        path = str(tmp_path / "data.yaml")
        atomic_write_yaml(path, {"todos": ["a"]}, "# header\n")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert text.startswith("# header\n")
        assert yaml.safe_load(text) == {"todos": ["a"]}

    def test_replace_retries_transient_lock(self, tmp_path, monkeypatch):
        """目标被短暂占用(PermissionError)时指数退避重试后成功."""
        path = str(tmp_path / "data.yaml")
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("target locked")
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        atomic_write_yaml(path, {"k": 1}, "# h\n")
        assert len(calls) == 3
        with open(path, encoding="utf-8") as f:
            assert yaml.safe_load(f.read()) == {"k": 1}

    def test_replace_exhausted_raises(self, tmp_path, monkeypatch):
        """持续占用重试耗尽后上抛 PermissionError(由调用方处理)."""
        path = str(tmp_path / "data.yaml")

        def locked_replace(_src, _dst):
            raise PermissionError("target locked")

        monkeypatch.setattr(os, "replace", locked_replace)
        with pytest.raises(PermissionError):
            atomic_write_yaml(path, {"k": 1}, "# h\n")


class TestEnsureFromTemplate:
    """模板播种."""

    def test_seeds_from_template(self, tmp_path):
        """缺失时复制模板, 返回 True; 再调用幂等返回 False."""
        tpl = tmp_path / "tpl.yaml"
        tpl.write_text("todos: []\n", encoding="utf-8")
        target = tmp_path / "sub" / "data.yaml"
        assert ensure_from_template(str(target), str(tpl), None) is True
        assert target.read_text(encoding="utf-8") == "todos: []\n"
        assert ensure_from_template(str(target), str(tpl), None) is False

    def test_falls_back_to_skeleton(self, tmp_path):
        """模板缺失时退化为写骨架."""
        target = tmp_path / "data.yaml"

        def write_skeleton(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("skeleton\n")

        assert ensure_from_template(str(target), None, write_skeleton) is True
        assert target.read_text(encoding="utf-8") == "skeleton\n"
