"""插件运行时: manifest 校验, 内置+外部双源发现, importlib 加载与启用状态.

权限是声明式门禁而非沙箱: 入口代码在宿主进程内运行, 本模块不做也无法做
运行时隔离. read 默认放行; write 只保证宿主在运行前先过确认; network
插件**默认禁用**, 显式启用(经宿主确认)即视为授权联网(2026-11-08 助教
确认的校园服务可选插件边界). 安全边界说明见 AGENTS.md 插件一节.

启用状态文件(state_path, 仿 links.yaml 的容错读写):

    disabled: [插件id, ...]          # 用户显式禁用
    network_enabled: [插件id, ...]   # network 插件的显式启用 = 授权联网
    # 程序维护, 手改会被重写; 出现在 disabled 即禁用(优先于 network_enabled)

语义约定: disabled = 不导入其代码(设计而非疏漏); 单个插件任何环节失败
只标记该插件 broken, 不影响宿主与其他插件.
"""

import importlib.util
import os
import re
from dataclasses import dataclass, field

import yaml

from shuimu.core.paths import (
    builtin_plugins_dir,
    default_plugins_state_path,
    external_plugins_dir,
)
from shuimu.core.yamlio import atomic_write_yaml

SUPPORTED_API_VERSION = 1

PARAM_TYPES = ("str", "bool", "int", "choice", "path", "secret")
KNOWN_PERMISSIONS = ("read", "write", "network")
RESOURCE_KINDS = ("file", "dir", "link", "app")   # app = 无文件目标动作型

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PARAM_KEYS = frozenset(
    {"name", "type", "label", "required", "default", "choices", "help"})
_MANIFEST_KEYS = frozenset(
    {"id", "name", "version", "api_version", "description", "entry",
     "permissions", "applies_to", "params"})

STATE_HEADER = ("# 水木工作台插件启用状态(程序维护, 手改会被重写).\n"
                "# disabled 为用户禁用的插件 id 清单; network_enabled 为\n"
                "# network 插件的显式启用清单(启用即授权联网), 其余默认启用.\n")


class ManifestError(ValueError):
    """manifest 非法(消息含原因), 由发现阶段捕获转为 broken 记录."""


class PluginError(Exception):
    """入口加载失败或状态不可用(消息含原因), 由调用方转为用户提示."""


@dataclass(frozen=True)
class PluginParam:
    """manifest 声明的一个参数."""

    name: str
    type: str            # PARAM_TYPES 之一
    label: str           # 表单/帮助里的显示名
    required: bool = False
    default: object = None
    choices: tuple = ()  # ((值, 标签), ...); choice 类型必填
    help: str = ""


@dataclass(frozen=True)
class PluginManifest:
    """解析后的插件清单."""

    id: str
    name: str
    version: str
    api_version: int = 1
    description: str = ""
    entry: str = "plugin"
    permissions: tuple = ("read",)
    applies_to: tuple = ("file", "dir", "link")
    params: tuple = ()


@dataclass
class PluginRecord:
    """一个插件的注册表记录(manifest 不可解析时为 None)."""

    manifest: object     # PluginManifest | None
    origin: str          # "builtin" | "external"
    directory: str
    status: str          # "ok" | "disabled" | "broken"
    error: str = ""
    warnings: tuple = ()
    loaded: object = None  # run 可调用缓存(首次导入后)


def _required_text(raw: dict, key: str, prefix: str) -> str:
    """读必填非空字符串字段."""
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{prefix}{key} 必填且不能为空")
    return value.strip()


def _version(raw: dict, prefix: str) -> str:
    """读 version: 仅展示用, 接受字符串/数字并归一为字符串."""
    value = raw.get("version")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) \
            or not str(value).strip():
        raise ManifestError(prefix + "version 必填且不能为空")
    return str(value).strip()


def _string_list(raw: dict, key: str, default: list, prefix: str,
                 warnings: list, known: tuple) -> tuple:
    """读字符串列表字段: 未知项降为 warning 丢弃, 返回去重元组."""
    value = raw.get(key, None)
    if value is None:
        return tuple(default)
    if not isinstance(value, list) \
            or not all(isinstance(item, str) for item in value):
        raise ManifestError(f"{prefix}{key} 须为字符串列表")
    kept, seen = [], set()
    for item in value:
        if known is not None and item not in known:
            warnings.append(f"未知 {key} 项: {item}(已忽略)")
            continue
        if item not in seen:
            seen.add(item)
            kept.append(item)
    return tuple(kept)


def _parse_choice(item: object, name: str, prefix: str) -> tuple:
    """归一单个候选: 标量 -> (值, 值字符串); [值, 标签] -> (值, 标签)."""
    if isinstance(item, (list, tuple)) and len(item) == 2:
        return item[0], str(item[1])
    if isinstance(item, (str, int, float)):
        return item, str(item)
    raise ManifestError(
        f"{prefix}参数 {name} 的 choices 元素非法: {item!r}")


def _parse_params(raw: object, prefix: str, warnings: list) -> tuple:
    """逐条校验 params 声明, 返回 PluginParam 元组."""
    if not isinstance(raw, list):
        raise ManifestError(prefix + "params 须为列表")
    params, seen = [], set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"{prefix}params[{i}] 须为映射")
        for key in sorted(set(item) - _PARAM_KEYS):
            warnings.append(f"未知参数键: {key}")
        name = item.get("name")
        if not isinstance(name, str) or not _IDENT_RE.match(name):
            raise ManifestError(f"{prefix}参数名非法: {name!r}")
        if name in seen:
            raise ManifestError(f"{prefix}参数名重复: {name}")
        seen.add(name)
        ptype = item.get("type")
        if ptype not in PARAM_TYPES:
            raise ManifestError(f"{prefix}参数 {name} 类型非法: {ptype!r}")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ManifestError(f"{prefix}参数 {name} 缺 label")
        required = item.get("required", False)
        if not isinstance(required, bool):
            raise ManifestError(f"{prefix}参数 {name} 的 required 须为布尔")
        default = item.get("default", None)
        if default is not None and ptype != "choice" and not (
            (ptype in ("str", "path", "secret") and isinstance(default, str))
            or (ptype == "int" and isinstance(default, int)
                and not isinstance(default, bool))
            or (ptype == "bool" and isinstance(default, bool))
        ):
            raise ManifestError(
                f"{prefix}参数 {name} 的 default 类型与 {ptype} 不符")
        choices = ()
        if ptype == "choice":
            raw_choices = item.get("choices")
            if not isinstance(raw_choices, list) or not raw_choices:
                raise ManifestError(
                    f"{prefix}参数 {name} 的 choice 须给 choices")
            choices = tuple(_parse_choice(c, name, prefix)
                            for c in raw_choices)
            if default is not None and \
                    str(default) not in {str(v) for v, _ in choices}:
                raise ManifestError(
                    f"{prefix}参数 {name} 的 default 不在候选内")
        help_text = item.get("help", "")
        if not isinstance(help_text, str):
            raise ManifestError(f"{prefix}参数 {name} 的 help 须为字符串")
        params.append(PluginParam(
            name, ptype, label.strip(), required, default, choices,
            help_text))
    return tuple(params)


def parse_manifest(raw: object, source: str = "") -> tuple:
    """dict -> (PluginManifest, warnings). 非法抛 ManifestError.

    未知键容忍并计入 warnings; 已知键类型严格.
    """
    prefix = f"{source}: " if source else ""
    if not isinstance(raw, dict):
        raise ManifestError(prefix + "manifest 须为映射")
    warnings = []
    manifest_id = _required_text(raw, "id", prefix)
    if not _ID_RE.match(manifest_id):
        raise ManifestError(f"{prefix}id 非法: {manifest_id!r}"
                            "(小写字母开头, 字母数字连字符, 2-32 字)")
    name = _required_text(raw, "name", prefix)
    version = _version(raw, prefix)
    api_version = raw.get("api_version", 1)
    if isinstance(api_version, bool) or not isinstance(api_version, int) \
            or api_version < 1:
        raise ManifestError(prefix + "api_version 须为正整数")
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ManifestError(prefix + "description 须为字符串")
    entry = raw.get("entry", "plugin")
    if not isinstance(entry, str) or not _IDENT_RE.match(entry):
        raise ManifestError(f"{prefix}entry 非法: {entry!r}")
    permissions = _string_list(raw, "permissions", ["read"], prefix,
                               warnings, KNOWN_PERMISSIONS)
    if not permissions:
        raise ManifestError(prefix + "permissions 不能为空")
    applies_to = _string_list(raw, "applies_to", list(RESOURCE_KINDS),
                              prefix, warnings, RESOURCE_KINDS)
    if not applies_to:
        raise ManifestError(prefix + "applies_to 不能为空")
    params = _parse_params(raw.get("params", []), prefix, warnings)
    for key in sorted(set(raw) - _MANIFEST_KEYS):
        warnings.append(f"未知 manifest 键: {key}")
    manifest = PluginManifest(
        manifest_id, name, version, api_version, description.strip(),
        entry, permissions, applies_to, params)
    return manifest, warnings


def _convert(param: PluginParam, value: object) -> tuple:
    """按声明类型转换单个参数; 返回 (值, None) 或 (None, 错误消息).

    字符串形态的输入来自 CLI --set; GUI 控件给原生类型.
    """
    if param.type in ("str", "secret"):
        return str(value).strip(), None
    if param.type == "path":
        return os.path.expanduser(str(value).strip()), None
    if param.type == "int":
        if isinstance(value, bool):
            return None, f"参数 {param.name} 须为整数"
        if isinstance(value, int):
            return value, None
        if isinstance(value, str):
            try:
                return int(value.strip()), None
            except ValueError:
                return None, f"参数 {param.name} 须为整数"
        return None, f"参数 {param.name} 须为整数"
    if param.type == "bool":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("true", "1", "yes", "on"):
                return True, None
            if text in ("false", "0", "no", "off", ""):
                return False, None
        return None, f"参数 {param.name} 须为布尔"
    values = {str(v) for v, _ in param.choices}
    if str(value) in values:
        return str(value), None
    return None, (f"参数 {param.name} 须为 "
                  + "/".join(str(v) for v, _ in param.choices))


def validate_params(manifest: PluginManifest, raw: object) -> tuple:
    """raw 参数 -> (params, errors). 纯函数, GUI/CLI 共用.

    未声明的键报错; required 缺失且无 default 报错; 缺失但有 default
    填 default; str/path 空串视为未提供. 返回的 dict 只含最终生效键.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, ["参数须为键值对"]
    errors = []
    declared = {p.name: p for p in manifest.params}
    for key in sorted(k for k in raw if k not in declared):
        errors.append(f"未知参数: {key}")
    params = {}
    for param in manifest.params:
        value = raw.get(param.name, None)
        if isinstance(value, str) and not value.strip() \
                and param.type in ("str", "path", "secret"):
            value = None
        if value is None:
            if param.default is not None:
                params[param.name] = _convert(param, param.default)[0]
            elif param.required:
                errors.append(f"缺少参数: {param.name}")
            continue
        converted, error = _convert(param, value)
        if error:
            errors.append(error)
        else:
            params[param.name] = converted
    return params, errors


@dataclass
class PluginsState:
    """启用状态文件内容(内存态与文件同构)."""

    disabled: set = field(default_factory=set)
    network_enabled: set = field(default_factory=set)


def load_state(path: str) -> PluginsState:
    """读 plugins.yaml; 缺失/损坏/结构非法 -> 全默认(disabled 优先)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return PluginsState()
    if not isinstance(data, dict):
        return PluginsState()

    def id_set(key: str) -> set:
        values = data.get(key)
        if not isinstance(values, list):
            return set()
        return {item for item in values if isinstance(item, str)}

    return PluginsState(disabled=id_set("disabled"),
                        network_enabled=id_set("network_enabled"))


def save_state(path: str, state: PluginsState) -> None:
    """全量重写状态文件. OSError 上抛调用方."""
    atomic_write_yaml(path, {"disabled": sorted(state.disabled),
                             "network_enabled": sorted(state.network_enabled)},
                      STATE_HEADER)


class PluginManager:
    """插件注册表. 构造即 discover; 之后记录驻内存, 状态变更即写回."""

    def __init__(self, builtin_dir: str = None, external_dir: str = None,
                 state_path: str = None):
        self._builtin_dir = builtin_dir or builtin_plugins_dir()
        self._external_dir = external_dir or external_plugins_dir()
        self._state_path = state_path or default_plugins_state_path()
        self._records = []                  # 内置(目录名排序)在先, 外部在后
        self._state = PluginsState()
        self.discover()

    def discover(self) -> None:
        """扫描两源并填充记录; 任何单点失败只影响该插件, 不抛异常."""
        self._records = []
        self._state = load_state(self._state_path)
        self._scan_dir(self._builtin_dir, "builtin")
        self._scan_dir(self._external_dir, "external")
        for record in self._records:
            if record.manifest is None or record.status != "ok":
                continue
            plugin_id = record.manifest.id
            if plugin_id in self._state.disabled:
                record.status = "disabled"
                record.error = "用户禁用"
            elif "network" in record.manifest.permissions \
                    and plugin_id not in self._state.network_enabled:
                record.status = "disabled"
                record.error = "network 权限: 默认禁用, 启用即授权联网"
        for record in self._records:
            if record.status == "ok":
                try:
                    self.entry(record.manifest.id)
                except PluginError:
                    continue   # entry() 已把记录标 broken

    def _scan_dir(self, root: str, origin: str) -> None:
        """遍历 root 下一级子目录(跳过 _ 开头与 __pycache__)."""
        if not root or not os.path.isdir(root):
            return
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return
        for name in names:
            directory = os.path.join(root, name)
            if name.startswith("_") or not os.path.isdir(directory):
                continue
            self._load_directory(directory, name, origin)

    def _load_directory(self, directory: str, dirname: str,
                        origin: str) -> None:
        """解析单个插件目录, 追加一条记录(ok/broken)."""
        manifest_path = os.path.join(directory, "manifest.yaml")
        if not os.path.isfile(manifest_path):
            return
        try:
            with open(manifest_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            self._records.append(PluginRecord(
                None, origin, directory, "broken", f"manifest 读取失败: {exc}"))
            return
        try:
            manifest, warnings = parse_manifest(raw, source=dirname)
        except ManifestError as exc:
            self._records.append(PluginRecord(
                None, origin, directory, "broken", str(exc)))
            return
        if dirname != manifest.id:
            warnings.append(f"目录名 {dirname} 与 id 不一致")
        if manifest.api_version > SUPPORTED_API_VERSION:
            self._records.append(PluginRecord(
                manifest, origin, directory, "broken",
                f"API 版本不兼容: 插件要求 {manifest.api_version}, "
                f"宿主支持 {SUPPORTED_API_VERSION}", tuple(warnings)))
            return
        clash = next((r for r in self._records
                      if r.manifest is not None
                      and r.manifest.id == manifest.id), None)
        if clash is not None:
            label = "内置" if clash.origin == "builtin" else "外部"
            self._records.append(PluginRecord(
                manifest, origin, directory, "broken",
                f"与{label}插件 id 冲突: {manifest.id}", tuple(warnings)))
            return
        self._records.append(PluginRecord(
            manifest, origin, directory, "ok", "", tuple(warnings)))

    def records(self) -> list:
        """全部记录(含 broken/disabled)."""
        return list(self._records)

    def catalog(self) -> list:
        """仅 ok 状态的 manifest 列表(GUI 菜单用)."""
        return [r.manifest for r in self._records if r.status == "ok"]

    def get(self, plugin_id: str):
        """按 id 取记录; 同 id 内置优先(记录列表内置在先)."""
        for record in self._records:
            if record.manifest is not None and record.manifest.id == plugin_id:
                return record
        return None

    def entry(self, plugin_id: str):
        """导入入口模块并返回其 run 可调用(缓存于 record.loaded).

        入口缺失/导入异常/未暴露 run -> 记录标 broken 并抛 PluginError.
        builtin 与 external 统一从文件路径加载(dev/frozen 行为一致,
        frozen 只需把 shuimu/plugins 整目录按 datas 打包). 模块名带
        _shuimu_plugin_ 前缀且不入 sys.modules, 重复发现互不干扰.
        """
        record = self.get(plugin_id)
        if record is None:
            raise PluginError(f"插件不存在: {plugin_id}")
        if record.loaded is not None:
            return record.loaded
        if record.manifest is None or record.status == "broken":
            raise PluginError(f"插件损坏: {record.error}")
        if record.status == "disabled":
            raise PluginError(f"插件已禁用: {plugin_id}")
        entry_path = os.path.join(record.directory,
                                  record.manifest.entry + ".py")
        module_name = ("_shuimu_plugin_"
                       + re.sub(r"[^A-Za-z0-9_]", "_", plugin_id))
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, entry_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"入口文件不可加载: {entry_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        # 插件代码任意异常都不能崩宿主, 此边界必须宽捕获
        except Exception as exc:  # pylint: disable=broad-exception-caught
            record.status = "broken"
            record.error = f"入口导入失败: {exc}"
            raise PluginError(record.error) from exc
        run = getattr(module, "run", None)
        if not callable(run):
            record.status = "broken"
            record.error = f"入口未暴露 run(): {os.path.basename(entry_path)}"
            raise PluginError(record.error)
        record.loaded = run
        return run

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """ok<->disabled 切换并写回状态文件.

        network 插件启用即加入 network_enabled(授权联网); 禁用同时移出
        network_enabled 并加入 disabled(双保险). broken/不存在抛
        PluginError; 启用即导入入口(失败标 broken 上抛); OSError 由调用方
        处理.
        """
        record = self.get(plugin_id)
        if record is None:
            raise PluginError(f"插件不存在: {plugin_id}")
        if record.status == "broken":
            raise PluginError(f"插件损坏, 无法切换: {record.error}")
        target = "ok" if enabled else "disabled"
        if record.status != target:
            if enabled:
                record.status = "ok"
                record.error = ""
                self._state.disabled.discard(plugin_id)
                if "network" in record.manifest.permissions:
                    self._state.network_enabled.add(plugin_id)
                try:
                    self.entry(plugin_id)
                except PluginError:
                    save_state(self._state_path, self._state)
                    raise
            else:
                record.status = "disabled"
                record.error = "用户禁用"
                record.loaded = None
                self._state.disabled.add(plugin_id)
                self._state.network_enabled.discard(plugin_id)
        save_state(self._state_path, self._state)
