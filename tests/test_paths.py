"""paths.py 单元测试: 路径解析与默认目录."""

import os
import sys

from shuimu.paths import default_db_path, default_workspace_root, project_root


class TestDefaultDbPath:
    """数据库默认位置."""

    def test_dev_mode_under_project(self):
        """源码运行时数据库在项目 data/ 下."""
        path = default_db_path()
        assert path.endswith(os.path.join("data", "shuimu.db"))
        assert project_root() in path

    def test_frozen_mode_uses_user_dir(self, monkeypatch, tmp_path):
        """打包后(frozen)落到平台用户数据目录."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = default_db_path()
        assert str(tmp_path) in path
        monkeypatch.delattr(sys, "frozen", raising=False)


class TestWorkspaceRoot:
    """中栏默认工作目录."""

    def test_defaults_to_content_when_present(self):
        """课程根目录存在 content/ 时以其为默认."""
        root = default_workspace_root()
        assert os.path.basename(root) == "content"
        assert os.path.isdir(root)
