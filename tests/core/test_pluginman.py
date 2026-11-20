"""pluginman.py 单元测试: manifest 校验, 双源发现, 加载与启用状态."""

import pytest

from shuimu.core.pluginman import (
    ManifestError,
    PluginError,
    PluginManager,
    PluginsState,
    load_state,
    parse_manifest,
    save_state,
    validate_params,
)
from tests.plugin_util import NONE_BODY
from tests.plugin_util import make_plugin_dir as make_plugin


@pytest.fixture
def dirs(tmp_path):
    """隔离三件套: builtin 目录 / external 目录 / 状态文件路径."""
    return (tmp_path / "builtin", tmp_path / "external", tmp_path / "state.yaml")


def make_manager(dirs):
    """用隔离目录构造 PluginManager."""
    builtin, external, state = dirs
    return PluginManager(builtin_dir=str(builtin), external_dir=str(external),
                         state_path=str(state))


def manifest_with_params(params):
    """造一个带 params 声明的 manifest(其余字段最小化)."""
    raw = {"id": "hw", "name": "n", "version": "1", "params": params}
    return parse_manifest(raw)[0]


class TestManifest:
    """parse_manifest 校验."""

    def test_minimal_defaults(self):
        """最小 manifest 全部取缺省值且无告警."""
        manifest, warnings = parse_manifest(
            {"id": "hw", "name": "自查", "version": "1"})
        assert manifest.api_version == 1
        assert manifest.entry == "plugin"
        assert manifest.permissions == ("read",)
        assert manifest.applies_to == ("file", "dir", "link", "app")
        assert not manifest.params
        assert not warnings

    def test_full_fields(self):
        """全字段解析: 权限/适用类型/choice 参数候选."""
        raw = {
            "id": "hwcheck", "name": "作业自查", "version": "1.2.0",
            "api_version": 1, "description": "检查作业", "entry": "main",
            "permissions": ["write", "read"], "applies_to": ["dir"],
            "params": [{"name": "rules", "type": "choice", "label": "规则",
                        "required": True, "default": "basic",
                        "choices": [["basic", "基础"], ["strict", "严格"]],
                        "help": "选规范"}],
        }
        manifest, warnings = parse_manifest(raw)
        assert manifest.permissions == ("write", "read")
        assert manifest.applies_to == ("dir",)
        assert manifest.params[0].choices == (
            ("basic", "基础"), ("strict", "严格"))
        assert not warnings

    def test_unknown_keys_warn_only(self):
        """未知 manifest 键与未知权限项降为告警并丢弃."""
        manifest, warnings = parse_manifest(
            {"id": "hw", "name": "n", "version": "1", "future_key": 1,
             "permissions": ["read", "telnet"]})
        assert "未知 manifest 键: future_key" in warnings
        assert "未知 permissions 项: telnet(已忽略)" in warnings
        assert manifest.permissions == ("read",)

    @pytest.mark.parametrize("bad_id", ["Bad", "1x", "x", "a" * 40, "has space"])
    def test_invalid_id_raises(self, bad_id):
        """id 不符小写-连字符约定抛 ManifestError."""
        with pytest.raises(ManifestError):
            parse_manifest({"id": bad_id, "name": "n", "version": "1"})

    def test_missing_name_raises(self):
        """缺必填字段抛 ManifestError."""
        with pytest.raises(ManifestError):
            parse_manifest({"id": "hw", "version": "1"})

    def test_bad_api_version_raises(self):
        """api_version 非正整数抛 ManifestError."""
        with pytest.raises(ManifestError):
            parse_manifest({"id": "hw", "name": "n", "version": "1",
                            "api_version": "2"})

    def test_choice_without_candidates_raises(self):
        """choice 参数不给候选抛 ManifestError."""
        with pytest.raises(ManifestError):
            manifest_with_params([{"name": "p", "type": "choice", "label": "P"}])

    def test_choice_default_out_of_range_raises(self):
        """choice 的 default 不在候选内抛 ManifestError."""
        with pytest.raises(ManifestError):
            manifest_with_params([
                {"name": "p", "type": "choice", "label": "P", "default": "nope",
                 "choices": [["a", "甲"]]}])

    def test_bad_param_type_raises(self):
        """参数类型不在 PARAM_TYPES 抛 ManifestError."""
        with pytest.raises(ManifestError):
            manifest_with_params([{"name": "p", "type": "float", "label": "P"}])

    def test_duplicate_param_names_raises(self):
        """参数名重复抛 ManifestError."""
        with pytest.raises(ManifestError):
            manifest_with_params([
                {"name": "p", "type": "str", "label": "P"},
                {"name": "p", "type": "int", "label": "P2"}])

    def test_default_type_mismatch_raises(self):
        """default 类型与声明不符抛 ManifestError."""
        with pytest.raises(ManifestError):
            manifest_with_params(
                [{"name": "p", "type": "int", "label": "P", "default": "x"}])

    def test_applies_to_all_unknown_raises(self):
        """applies_to 全部未知丢弃后为空抛 ManifestError."""
        with pytest.raises(ManifestError):
            parse_manifest({"id": "hw", "name": "n", "version": "1",
                            "applies_to": ["course"]})

    def test_source_prefix_in_message(self):
        """带 source 时错误消息含目录名前缀."""
        with pytest.raises(ManifestError, match="myplugin"):
            parse_manifest({"id": "hw", "name": "", "version": "1"},
                           source="myplugin")

    def test_app_kind_and_secret_type(self):
        """app 目标类型与 secret 参数类型合法."""
        manifest, warnings = parse_manifest({
            "id": "campus", "name": "校园", "version": "1",
            "applies_to": ["app"],
            "params": [{"name": "password", "type": "secret",
                        "label": "密码"}]})
        assert manifest.applies_to == ("app",)
        assert manifest.params[0].type == "secret"
        assert not warnings


class TestValidateParams:
    """validate_params 转换与校验(GUI/CLI 共用)."""

    def test_default_filled(self):
        """缺失参数取 default."""
        manifest = manifest_with_params(
            [{"name": "n", "type": "int", "label": "N", "default": 5}])
        params, errors = validate_params(manifest, {})
        assert params == {"n": 5}
        assert not errors

    def test_required_missing_raises_error(self):
        """required 缺失且无 default 报错."""
        manifest = manifest_with_params(
            [{"name": "n", "type": "str", "label": "N", "required": True}])
        params, errors = validate_params(manifest, {})
        assert not params
        assert errors == ["缺少参数: n"]

    def test_unknown_key_error(self):
        """未声明的键报错."""
        manifest = manifest_with_params([])
        params, errors = validate_params(manifest, {"ghost": 1})
        assert not params
        assert errors == ["未知参数: ghost"]

    def test_int_from_string(self):
        """字符串 "3" 转为整数 3."""
        manifest = manifest_with_params(
            [{"name": "n", "type": "int", "label": "N"}])
        params, errors = validate_params(manifest, {"n": "3"})
        assert params == {"n": 3}
        assert not errors

    def test_int_from_bad_string_errors(self):
        """非数字字符串报整数错误."""
        manifest = manifest_with_params(
            [{"name": "n", "type": "int", "label": "N"}])
        _, errors = validate_params(manifest, {"n": "abc"})
        assert errors == ["参数 n 须为整数"]

    def test_bool_variants(self):
        """原生 bool 与字符串 true/false 均可, 其余报错."""
        manifest = manifest_with_params(
            [{"name": "b", "type": "bool", "label": "B"}])
        assert validate_params(manifest, {"b": True})[0] == {"b": True}
        assert validate_params(manifest, {"b": "true"})[0] == {"b": True}
        assert validate_params(manifest, {"b": "no"})[0] == {"b": False}
        assert validate_params(manifest, {"b": "maybe"})[1] == ["参数 b 须为布尔"]

    def test_choice_membership(self):
        """choice 命中候选归一为字符串, 越界报错."""
        manifest = manifest_with_params([
            {"name": "c", "type": "choice", "label": "C",
             "choices": [["basic", "基础"], ["strict", "严格"]]}])
        params, errors = validate_params(manifest, {"c": "strict"})
        assert params == {"c": "strict"}
        assert not errors
        _, errors = validate_params(manifest, {"c": "nope"})
        assert errors[0].startswith("参数 c 须为")

    def test_path_expanduser(self, tmp_path, monkeypatch):
        """path 类型做 expanduser."""
        monkeypatch.setenv("HOME", str(tmp_path))          # POSIX
        monkeypatch.setenv("USERPROFILE", str(tmp_path))   # Windows ntpath 走 USERPROFILE
        manifest = manifest_with_params(
            [{"name": "p", "type": "path", "label": "P"}])
        params, _ = validate_params(manifest, {"p": "~/rules.yaml"})
        assert params["p"] == str(tmp_path / "rules.yaml")

    def test_optional_empty_string_omitted(self):
        """可选参数空串视为未提供, 键不出现."""
        manifest = manifest_with_params(
            [{"name": "s", "type": "str", "label": "S", "default": "d"}])
        params, errors = validate_params(manifest, {"s": "  "})
        assert params == {"s": "d"}
        assert not errors

    def test_raw_none_treated_empty(self):
        """raw 传 None 视为空参数集."""
        manifest = manifest_with_params([])
        assert validate_params(manifest, None) == ({}, [])

    def test_secret_param_validates_like_str(self):
        """secret 参数按 str 转换(去空白, 空串视为未提供)."""
        manifest = manifest_with_params(
            [{"name": "code", "type": "secret", "label": "验证码"}])
        assert validate_params(manifest, {"code": " 123456 "}) == \
            ({"code": "123456"}, [])
        assert validate_params(manifest, {"code": "  "}) == ({}, [])


class TestDiscover:
    """双源发现与坏插件容错."""

    def test_builtin_and_external_origins(self, dirs):
        """两源各自发现, origin 正确, catalog 依目录名排序."""
        make_plugin(dirs[0], "alpha")
        make_plugin(dirs[1], "beta")
        manager = make_manager(dirs)
        by_id = {r.manifest.id: r for r in manager.records()}
        assert by_id["alpha"].origin == "builtin"
        assert by_id["beta"].origin == "external"
        assert [m.id for m in manager.catalog()] == ["alpha", "beta"]

    def test_external_id_conflict_marks_external_broken(self, dirs):
        """外部与内置同 id: 内置优先, 外部标 broken."""
        make_plugin(dirs[0], "dupe")
        make_plugin(dirs[1], "dupe")
        manager = make_manager(dirs)
        assert manager.get("dupe").origin == "builtin"
        external = [r for r in manager.records() if r.origin == "external"]
        assert external[0].status == "broken"
        assert "冲突" in external[0].error

    def test_dir_without_manifest_skipped(self, dirs):
        """无 manifest.yaml 的目录跳过."""
        (dirs[0] / "notaplugin").mkdir(parents=True)
        assert not make_manager(dirs).records()

    def test_corrupt_manifest_broken(self, dirs):
        """manifest YAML 损坏标记 broken, 不抛异常."""
        directory = dirs[0] / "bad"
        directory.mkdir(parents=True)
        (directory / "manifest.yaml").write_text("id: [unclosed",
                                                 encoding="utf-8")
        record = make_manager(dirs).records()[0]
        assert record.status == "broken"
        assert record.manifest is None
        assert "manifest 读取失败" in record.error

    def test_entry_without_run_broken(self, dirs):
        """入口未暴露 run 标记 broken."""
        make_plugin(dirs[0], "norun", body="X = 1\n")
        record = make_manager(dirs).get("norun")
        assert record.status == "broken"
        assert "run" in record.error

    def test_entry_import_error_broken(self, dirs):
        """入口导入抛异常标记 broken, 宿主不崩."""
        make_plugin(dirs[0], "boom", body="raise ZeroDivisionError('x')\n")
        assert make_manager(dirs).get("boom").status == "broken"

    def test_network_plugin_disabled_by_default(self, dirs):
        """network 插件默认禁用(非 broken), 原因可展示."""
        make_plugin(dirs[0], "net", manifest={"permissions": ["network"]})
        record = make_manager(dirs).get("net")
        assert record.status == "disabled"
        assert "network" in record.error
        assert "默认禁用" in record.error

    def test_network_enable_roundtrip(self, dirs):
        """network 插件启用写 network_enabled, 新 manager 读回可用."""
        make_plugin(dirs[0], "net", manifest={"permissions": ["network"]})
        manager = make_manager(dirs)
        manager.set_enabled("net", True)
        assert manager.get("net").status == "ok"
        text = dirs[2].read_text(encoding="utf-8")
        assert "network_enabled" in text and "net" in text
        fresh = make_manager(dirs)
        assert fresh.get("net").status == "ok"
        fresh.set_enabled("net", False)
        assert fresh.get("net").status == "disabled"
        again = make_manager(dirs)
        assert again.get("net").status == "disabled"
        assert "用户禁用" in again.get("net").error

    def test_old_state_file_backward_compatible(self, dirs):
        """旧版状态文件(只有 disabled 键)读入后 network_enabled 为空."""
        make_plugin(dirs[0], "net", manifest={"permissions": ["network"]})
        dirs[2].write_text("disabled: []\n", encoding="utf-8")
        state = load_state(str(dirs[2]))
        assert not state.disabled and not state.network_enabled
        assert make_manager(dirs).get("net").status == "disabled"

    def test_disabled_wins_over_network_enabled(self, dirs):
        """同时出现在两个清单时 disabled 优先."""
        make_plugin(dirs[0], "net", manifest={"permissions": ["network"]})
        dirs[2].write_text(
            "disabled: [net]\nnetwork_enabled: [net]\n", encoding="utf-8")
        record = make_manager(dirs).get("net")
        assert record.status == "disabled"
        assert "用户禁用" in record.error

    def test_api_version_too_new_broken(self, dirs):
        """api_version 超过宿主支持版本标记 broken."""
        make_plugin(dirs[0], "future", manifest={"api_version": 2})
        record = make_manager(dirs).get("future")
        assert record.status == "broken"
        assert "API 版本不兼容" in record.error

    def test_dirname_mismatch_warns(self, dirs):
        """目录名与 id 不一致仅告警."""
        make_plugin(dirs[0], "foldername", manifest={"id": "realid"})
        record = make_manager(dirs).get("realid")
        assert record.status == "ok"
        assert any("不一致" in w for w in record.warnings)

    def test_missing_dirs_tolerated(self, tmp_path):
        """目录不存在时发现为空, 不抛异常."""
        manager = PluginManager(
            builtin_dir=str(tmp_path / "none"),
            external_dir=str(tmp_path / "none2"),
            state_path=str(tmp_path / "s.yaml"))
        assert not manager.records()

    def test_disabled_plugin_not_imported(self, dirs):
        """disabled 语义 = 不导入其代码(入口无副作用)."""
        marker = dirs[0].parent / "marker"
        body = (f"import pathlib\npathlib.Path({str(marker)!r}).touch()\n"
                + NONE_BODY)
        make_plugin(dirs[0], "later", body=body)
        make_manager(dirs)   # 启用态: 导入发生
        assert marker.exists()
        marker.unlink()
        save_state(str(dirs[2]), PluginsState(disabled={"later"}))
        manager = make_manager(dirs)   # 禁用态: 不导入
        assert not marker.exists()
        assert manager.get("later").status == "disabled"


class TestEntry:
    """入口加载与缓存."""

    def test_returns_run_and_caches(self, dirs):
        """返回可调用且二次调用命中缓存."""
        make_plugin(dirs[0], "alpha")
        manager = make_manager(dirs)
        run_first = manager.entry("alpha")
        run_second = manager.entry("alpha")
        assert callable(run_first)
        assert run_first is run_second
        assert manager.get("alpha").loaded is run_first

    def test_missing_plugin_raises(self, dirs):
        """不存在的插件抛 PluginError."""
        with pytest.raises(PluginError):
            make_manager(dirs).entry("ghost")

    def test_disabled_raises(self, dirs):
        """禁用插件的入口不可取."""
        make_plugin(dirs[0], "alpha")
        save_state(str(dirs[2]), PluginsState(disabled={"alpha"}))
        with pytest.raises(PluginError, match="已禁用"):
            make_manager(dirs).entry("alpha")


class TestState:
    """启用状态持久化."""

    def test_set_enabled_roundtrip(self, dirs):
        """禁用写盘, 新 manager 读回; 启用后恢复."""
        make_plugin(dirs[0], "alpha")
        manager = make_manager(dirs)
        manager.set_enabled("alpha", False)
        assert manager.get("alpha").status == "disabled"
        fresh = make_manager(dirs)
        assert fresh.get("alpha").status == "disabled"
        fresh.set_enabled("alpha", True)
        assert fresh.get("alpha").status == "ok"
        assert make_manager(dirs).get("alpha").status == "ok"

    def test_state_file_has_header_and_ids(self, dirs):
        """状态文件含头部说明与 id 清单."""
        make_plugin(dirs[0], "alpha")
        make_manager(dirs).set_enabled("alpha", False)
        text = dirs[2].read_text(encoding="utf-8")
        assert "程序维护" in text
        assert "alpha" in text

    def test_corrupt_state_all_enabled(self, dirs):
        """损坏状态文件容错为全启用."""
        make_plugin(dirs[0], "alpha")
        dirs[2].write_text("!!!bad yaml[", encoding="utf-8")
        assert make_manager(dirs).get("alpha").status == "ok"

    def test_load_state_non_dict_returns_empty(self, tmp_path):
        """状态文件顶层非映射返回全默认状态."""
        path = tmp_path / "s.yaml"
        path.write_text("- a\n", encoding="utf-8")
        state = load_state(str(path))
        assert not state.disabled
        assert not state.network_enabled

    def test_set_enabled_broken_rejected(self, dirs):
        """broken 插件拒绝切换状态."""
        make_plugin(dirs[0], "boom", body="raise RuntimeError()\n")
        with pytest.raises(PluginError):
            make_manager(dirs).set_enabled("boom", True)

    def test_enable_broken_on_import_marks_broken(self, dirs):
        """启用后入口导入失败则标 broken 并上抛."""
        make_plugin(dirs[0], "boom", body="raise RuntimeError()\n")
        save_state(str(dirs[2]), PluginsState(disabled={"boom"}))
        manager = make_manager(dirs)
        with pytest.raises(PluginError):
            manager.set_enabled("boom", True)
        assert manager.get("boom").status == "broken"
