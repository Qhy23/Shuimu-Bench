"""测试共用: 造插件目录的辅助(core 与 cli/controller 测试共用)."""

import yaml

NONE_BODY = "def run(job):\n    return None\n"


def make_plugin_dir(root, dirname, manifest=None, body=NONE_BODY):
    """在 root/<dirname> 造一个最小插件目录(id 缺省同目录名)."""
    directory = root / dirname
    directory.mkdir(parents=True)
    raw = {"id": dirname, "name": dirname, "version": "1.0"}
    if manifest:
        raw.update(manifest)
    (directory / "manifest.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    (directory / "plugin.py").write_text(body, encoding="utf-8")
    return directory
