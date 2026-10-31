"""paths.py 单元测试: 路径解析与默认目录."""

import os
import sys

import yaml

from shuimu.core.paths import (
    default_db_path,
    default_links_path,
    default_schedule_path,
    default_workspace_root,
    project_root,
    template_links_path,
    template_schedule_path,
)


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


class TestConfigYamlExample:
    """项目根的示例 config.yaml."""

    def test_example_file_valid_and_loadable(self):
        """示例文件可解析且键结构与默认值一致."""
        example = os.path.join(project_root(), "config.yaml")
        with open(example, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert set(data) == {"app", "course", "organize", "schedule"}
        assert data["course"]["default_name"] == "未分组"
        assert "文档" in data["organize"]["categories"]


class TestSchedulePaths:
    """日程数据文件与模板路径."""

    def test_dev_mode_under_project(self):
        """源码运行时数据文件与模板都在项目 data/ 下."""
        path = default_schedule_path()
        assert path.endswith(os.path.join("data", "schedule.yaml"))
        assert project_root() in path
        assert template_schedule_path().endswith("schedule.example.yaml")

    def test_frozen_mode_uses_user_dir(self, monkeypatch, tmp_path):
        """打包后数据文件落到平台用户数据目录."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = default_schedule_path()
        assert str(tmp_path) in path
        monkeypatch.delattr(sys, "frozen", raising=False)


class TestLinksPaths:
    """常用链接数据文件与模板路径."""

    def test_dev_mode_under_project(self):
        """源码运行时数据文件与模板都在项目 data/ 下."""
        path = default_links_path()
        assert path.endswith(os.path.join("data", "links.yaml"))
        assert project_root() in path
        assert template_links_path().endswith("links.example.yaml")

    def test_frozen_mode_uses_user_dir(self, monkeypatch, tmp_path):
        """打包后数据文件落到平台用户数据目录."""
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        path = default_links_path()
        assert str(tmp_path) in path
        monkeypatch.delattr(sys, "frozen", raising=False)
