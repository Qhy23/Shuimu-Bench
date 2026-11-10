"""GUI 测试共用 fixture."""

import pytest


@pytest.fixture(autouse=True)
def plugins_isolated(tmp_path, monkeypatch):
    """插件状态/外部目录/校园会话路径隔离到 tmp(内置目录仍用真实仓库插件).

    GUI 测试普遍构造 MainWindow -> 触发插件发现与校园页签读会话,
    autouse 保证不碰真实用户目录与项目 data/ 下文件.
    """
    monkeypatch.setattr("shuimu.core.pluginman.default_plugins_state_path",
                        lambda: str(tmp_path / "plugins.yaml"))
    monkeypatch.setattr("shuimu.core.pluginman.external_plugins_dir",
                        lambda: str(tmp_path / "ext-plugins"))
    monkeypatch.setattr("shuimu.core.controller.default_campus_session_path",
                        lambda: str(tmp_path / "campus-session.json"))
    monkeypatch.setattr(
        "shuimu.core.controller.default_campus_fingerprint_path",
        lambda: str(tmp_path / "campus-fingerprint"))
