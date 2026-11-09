"""GUI 测试共用 fixture."""

import pytest


@pytest.fixture(autouse=True)
def plugins_isolated(tmp_path, monkeypatch):
    """插件状态与外部目录隔离到 tmp(内置目录仍用真实仓库插件).

    GUI 测试普遍构造 MainWindow -> 触发插件发现, autouse 保证不碰
    真实用户目录与项目 data/plugins.yaml.
    """
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(tmp_path / "ext-plugins"))
