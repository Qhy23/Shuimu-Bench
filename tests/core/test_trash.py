"""trash.py 单元测试: 回收站后端.

一律用注入 trash_dir 的配置指向 tmp_path, 绝不触碰真实
~/.local/share/Trash; Windows 分支只测分发决策, 不测 ctypes 调用.
"""

import os
from copy import deepcopy
from datetime import datetime

import pytest

from shuimu.core.config import DEFAULTS
from shuimu.core.paths import project_root
from shuimu.core.trash import (
    BACKEND_CONFIG_DIR,
    BACKEND_WINDOWS_SHELL,
    BACKEND_XDG,
    move_to_trash,
    resolve_trash_root,
    trash_backend,
)


@pytest.fixture()
def cfg(tmp_path):
    """trash_dir 指向临时目录的配置."""
    base = deepcopy(DEFAULTS)
    base["fileops"]["trash_dir"] = str(tmp_path / "trash")
    return base


class TestMoveToTrash:
    """移入回收站(config_dir 后端)."""

    def test_file(self, tmp_path, cfg):
        """文件入 files/, info 记录原路径与可解析的删除时间."""
        f = tmp_path / "a.txt"
        f.write_text("x")
        result = move_to_trash(str(f), cfg)
        assert result.backend == BACKEND_CONFIG_DIR
        assert not f.exists()
        root = tmp_path / "trash"
        assert (root / "files" / "a.txt").read_text() == "x"
        info = (root / "info" / "a.txt.trashinfo").read_text(encoding="utf-8")
        lines = info.splitlines()
        assert lines[0] == "[Trash Info]"
        assert lines[1] == f"Path={f.resolve()}"
        deletion = lines[2].removeprefix("DeletionDate=")
        datetime.fromisoformat(deletion)  # 能解析即合法

    def test_directory(self, tmp_path, cfg):
        """目录整树进入 files/."""
        d = tmp_path / "d"
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "x.txt").write_text("x")
        move_to_trash(str(d), cfg)
        assert not d.exists()
        assert (tmp_path / "trash" / "files" / "d" / "sub" / "x.txt").exists()

    def test_same_name_twice(self, tmp_path, cfg):
        """同名二次删除: files 与 info 各得 _1 副本, 互不覆盖."""
        for text in ("first", "second"):
            f = tmp_path / "a.txt"
            f.write_text(text)
            move_to_trash(str(f), cfg)
        files = tmp_path / "trash" / "files"
        assert (files / "a.txt").read_text() == "first"
        assert (files / "a_1.txt").read_text() == "second"
        info = tmp_path / "trash" / "info"
        assert (info / "a.txt.trashinfo").exists()
        assert (info / "a_1.txt.trashinfo").exists()

    def test_special_chars_encoded(self, tmp_path, cfg):
        """文件名/路径中的 % # ? 按规范百分号转义."""
        f = tmp_path / "a#b?c%d.txt"
        f.write_text("x")
        move_to_trash(str(f), cfg)
        info = tmp_path / "trash" / "info" / "a#b?c%d.txt.trashinfo"
        text = info.read_text(encoding="utf-8")
        encoded = (str(f.resolve())
                   .replace("%", "%25").replace("#", "%23").replace("?", "%3F"))
        assert f"Path={encoded}" in text

    def test_missing_source_raises(self, tmp_path, cfg):
        """源不存在抛 OSError."""
        with pytest.raises(OSError):
            move_to_trash(str(tmp_path / "nope"), cfg)


class TestBackendDispatch:
    """后端分发决策(纯函数, platform 注入)."""

    def test_config_dir_wins(self, cfg):
        """配置了 trash_dir 时任何平台都用 config_dir."""
        assert trash_backend(cfg, "win32") == BACKEND_CONFIG_DIR
        assert trash_backend(cfg, "linux") == BACKEND_CONFIG_DIR

    def test_windows_shell(self):
        """未配置 + win32 -> Shell 回收站."""
        assert trash_backend(DEFAULTS, "win32") == BACKEND_WINDOWS_SHELL

    def test_xdg(self):
        """未配置 + linux -> XDG Trash."""
        assert trash_backend(DEFAULTS, "linux") == BACKEND_XDG


class TestResolveTrashRoot:
    """回收站根目录解析."""

    def test_config_dir_absolute(self, tmp_path, cfg):
        """绝对路径配置原样(展开 ~ 后)返回."""
        assert resolve_trash_root(cfg) == str(tmp_path / "trash")

    def test_config_dir_relative_to_project(self):
        """相对路径按项目根解析, 不随 CWD 漂移."""
        cfg = deepcopy(DEFAULTS)
        cfg["fileops"]["trash_dir"] = "data/trash"
        assert resolve_trash_root(cfg) == os.path.join(
            project_root(), "data", "trash")

    def test_xdg_env(self, tmp_path, monkeypatch):
        """XDG_DATA_HOME 设置时落在其下 Trash/."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert resolve_trash_root(DEFAULTS) == str(tmp_path / "Trash")

    def test_xdg_default(self, monkeypatch):
        """未设环境变量时回退 ~/.local/share/Trash."""
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        expected = os.path.join(os.path.expanduser("~"), ".local", "share", "Trash")
        assert resolve_trash_root(DEFAULTS) == expected
