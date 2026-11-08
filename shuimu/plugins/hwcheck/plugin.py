"""作业提交自查插件: 按规则包对目录做只读检查, 结论写入 PluginReport.

五类检查: 必交文件(required_files) / 禁止文件(forbidden_files) /
文件名合规(name_checks) / 密钥泄漏(secret_scan) / 大文件(large_file_mb).
规则包为 YAML(内置 basic/strict 两包, rules_path 可给自定义路径);
内置密钥清单始终生效, 规则包只能追加不能关闭. 全程只读, 不修改文件.
"""

import glob as globmod
import os
import re
from fnmatch import fnmatch

import yaml

from shuimu.core.paths import builtin_plugins_dir
from shuimu.core.pluginapi import (
    PluginJob,
    PluginReport,
    finding,
    make_report,
)

BUILTIN_PACKS = ("basic", "strict")
DEFAULT_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules",
                     ".idea", ".vscode"}
MAX_SCAN_BYTES = 2 * 1024 * 1024   # 单文件超过则跳过内容扫描
MAX_DETAIL_LINES = 10              # finding.detail 最多列 10 处命中
MAX_LARGE_ITEMS = 20               # 大文件警告最多列 20 条

_SEVERITY_ORDER = {"ok": 0, "warn": 1, "error": 2}

# 内置密钥清单: (标签, 正则, 严重度); 规则包只能追加不能关闭
DEFAULT_SECRET_PATTERNS = (
    ("私钥文件头(PEM)",
     r"-----BEGIN (RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"
     r"( BLOCK)?-----", "error"),
    ("AWS Access Key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "error"),
    ("GitHub Token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b", "error"),
    ("GitHub App Token", r"\bgithub_pat_[A-Za-z0-9_]{22,}\b", "error"),
    ("OpenAI/Anthropic 风格密钥",
     r"\bsk-(?:proj-|svc-|ant-)[A-Za-z0-9_-]{20,}\b", "error"),
    ("通用 sk- 密钥", r"\bsk-[A-Za-z0-9_-]{32,}\b", "warn"),
    ("Slack Token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "error"),
    ("Google API Key", r"\bAIza[0-9A-Za-z_\-]{35}\b", "error"),
    ("阿里云 AccessKey ID", r"\bLTAI[0-9A-Za-z]{12,20}\b", "error"),
    ("腾讯云 SecretId", r"\bAKID[0-9A-Za-z]{13,40}\b", "error"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*",
     "warn"),
    ("疑似密码赋值",
     r"(?i)\b(?:api_?key|secret|token|passwd|password)\b\s*[:=]\s*"
     r"['\"]?[A-Za-z0-9_\-/+]{16,}", "warn"),
)
DEFAULT_SECRET_COMPILED = tuple(
    (label, re.compile(pattern), severity)
    for label, pattern, severity in DEFAULT_SECRET_PATTERNS)

# 内容扫描覆盖的文本扩展名(规则包 secret_scan.extensions 可覆盖)
SCAN_EXTENSIONS = {".py", ".ipynb", ".md", ".txt", ".json", ".yaml", ".yml",
                   ".toml", ".ini", ".cfg", ".conf", ".env", ".js", ".ts",
                   ".tsx", ".jsx", ".html", ".css", ".sh", ".bat", ".ps1",
                   ".c", ".cpp", ".h", ".java", ".go", ".rs", ".r", ".m",
                   ".sql", ".tex", ".bib"}


def rules_dir() -> str:
    """内置规则包目录(dev/frozen 经 paths 分发一致)."""
    return os.path.join(builtin_plugins_dir(), "hwcheck", "rules")


def _compile(pattern: str, where: str) -> re.Pattern:
    """编译正则, 非法抛 ValueError(消息含出处)."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{where} 正则非法: {pattern}({exc})") from exc


def _parse_secret(raw: object, prefix: str) -> dict:
    """解析 secret_scan 段: extensions 可覆盖, patterns 追加到内置清单."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(prefix + "secret_scan 须为映射")
    extensions = SCAN_EXTENSIONS
    if "extensions" in raw:
        items = raw["extensions"]
        if not isinstance(items, list) \
                or not all(isinstance(i, str) for i in items):
            raise ValueError(prefix + "secret_scan.extensions 须为字符串列表")
        extensions = {i if i.startswith(".") else f".{i}" for i in items}
    patterns = list(DEFAULT_SECRET_COMPILED)
    items = raw.get("patterns", [])
    if not isinstance(items, list):
        raise ValueError(prefix + "secret_scan.patterns 须为列表")
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("pattern"),
                                                        str):
            raise ValueError(
                f"{prefix}secret_scan.patterns[{i}] 缺 pattern")
        severity = item.get("severity", "error")
        if severity not in _SEVERITY_ORDER:
            raise ValueError(
                f"{prefix}secret_scan.patterns[{i}] severity 非法: {severity}")
        patterns.append((str(item.get("label", item["pattern"])),
                         _compile(item["pattern"],
                                  f"secret_scan.patterns[{i}]"),
                         severity))
    return {"extensions": extensions, "patterns": patterns}


def parse_rulepack(raw: dict, source: str = "") -> dict:
    """校验并归一规则包: 预编译正则; 非法抛 ValueError; 未知键入 _warnings."""
    prefix = f"{source}: " if source else ""
    if not isinstance(raw, dict):
        raise ValueError(prefix + "规则包须为映射")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(prefix + "规则包缺 name")
    warnings = []
    known = {"name", "description", "required_files", "forbidden_files",
             "name_checks", "secret_scan"}
    for key in sorted(set(raw) - known):
        warnings.append(f"规则包未知键: {key}")

    def glob_list(key):
        """解析 required_files / forbidden_files 段."""
        items = raw.get(key, [])
        if not isinstance(items, list):
            raise ValueError(f"{prefix}{key} 须为列表")
        parsed = []
        for i, item in enumerate(items):
            if not isinstance(item, dict) \
                    or not isinstance(item.get("pattern"), str):
                raise ValueError(f"{prefix}{key}[{i}] 缺 pattern")
            parsed.append({"pattern": item["pattern"],
                           "label": str(item.get("label", item["pattern"]))})
        return parsed

    checks = []
    items = raw.get("name_checks", [])
    if not isinstance(items, list):
        raise ValueError(prefix + "name_checks 须为列表")
    for i, item in enumerate(items):
        if not isinstance(item, dict) \
                or not isinstance(item.get("applies"), str) \
                or not isinstance(item.get("pattern"), str):
            raise ValueError(f"{prefix}name_checks[{i}] 缺 applies/pattern")
        severity = item.get("severity", "error")
        if severity not in _SEVERITY_ORDER:
            raise ValueError(
                f"{prefix}name_checks[{i}] severity 非法: {severity}")
        checks.append({"applies": item["applies"],
                       "compiled": _compile(item["pattern"],
                                            f"name_checks[{i}]"),
                       "label": str(item.get("label", item["pattern"])),
                       "severity": severity})

    secret = _parse_secret(raw.get("secret_scan", None), prefix)
    return {"name": name.strip(),
            "description": str(raw.get("description", "")),
            "required_files": glob_list("required_files"),
            "forbidden_files": glob_list("forbidden_files"),
            "name_checks": checks,
            "secret_patterns": secret["patterns"],
            "scan_extensions": secret["extensions"],
            "_warnings": warnings}


def load_rules(spec: str) -> dict:
    """加载规则包: 内置包名或 YAML 文件路径; 皆非抛 ValueError."""
    path = str(spec)
    if spec in BUILTIN_PACKS:
        path = os.path.join(rules_dir(), f"{spec}.yaml")
    if not os.path.isfile(path):
        raise ValueError(f"规则包不存在: {spec}")
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"规则包读取失败: {exc}") from exc
    return parse_rulepack(raw, source=os.path.basename(path))


def _excludes(raw: object) -> list:
    """逗号分隔的排除 glob 拆分为列表."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _excluded(rel: str, excludes: list) -> bool:
    """相对路径自身或其任一祖先目录命中排除模式(fnmatch, * 可跨段)."""
    if not excludes:
        return False
    parts = rel.split("/")
    for i in range(len(parts)):
        prefix = "/".join(parts[:i + 1])
        if any(fnmatch(prefix, pattern) for pattern in excludes):
            return True
    return False


def _walk_files(root: str, excludes: list) -> list:
    """收集相对路径文件清单(posix 风格), 剪掉默认跳过目录与排除项."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames[:] = [d for d in dirnames
                       if d not in DEFAULT_SKIP_DIRS
                       and not _excluded(
                           d if rel_dir == "." else f"{rel_dir}/{d}",
                           excludes)]
        for fname in filenames:
            rel = fname if rel_dir == "." else f"{rel_dir}/{fname}"
            if not _excluded(rel, excludes):
                files.append(rel)
    return files


def _glob_files(root: str, pattern: str) -> list:
    """glob 相对根目录, 返回 posix 相对路径(仅文件, 已排序)."""
    hits = globmod.glob(os.path.join(
        root, pattern.replace("/", os.sep)), recursive=True)
    return sorted(
        os.path.relpath(h, root).replace(os.sep, "/")
        for h in hits if os.path.isfile(h))


def _cap_lines(items: list) -> str:
    """命中清单转 detail 文本, 超 MAX_DETAIL_LINES 加省略行."""
    text = "\n".join(items[:MAX_DETAIL_LINES])
    if len(items) > MAX_DETAIL_LINES:
        text += f"\n...等 {len(items)} 处"
    return text


def check_required(root: str, rules: dict, excludes: list) -> list:
    """必交文件: glob 命中 -> ok finding, 未命中 -> error finding."""
    findings = []
    for rule in rules["required_files"]:
        hits = [p for p in _glob_files(root, rule["pattern"])
                if not _excluded(p, excludes)]
        if hits:
            findings.append(finding("ok", "required_files",
                                    f"已包含 {rule['label']}", hits[0]))
        else:
            findings.append(finding("error", "required_files",
                                    f"缺少必交文件 {rule['label']}",
                                    f"期望匹配 {rule['pattern']}"))
    return findings


def check_forbidden(root: str, rules: dict, excludes: list) -> list:
    """禁止文件: 每条命中的规则一个 error finding."""
    findings = []
    for rule in rules["forbidden_files"]:
        hits = [p for p in _glob_files(root, rule["pattern"])
                if not _excluded(p, excludes)]
        if hits:
            findings.append(finding("error", "forbidden_files",
                                    f"发现禁止项 {rule['label']}",
                                    _cap_lines(hits)))
    return findings


def check_names(rules: dict, files: list) -> list:
    """文件名合规: applies 命中的文件, 文件名不匹配 pattern 则报."""
    findings = []
    for rule in rules["name_checks"]:
        targets = [f for f in files
                   if fnmatch(f, rule["applies"])
                   or fnmatch(os.path.basename(f), rule["applies"])]
        if not targets:
            continue
        bad = [f for f in targets
               if not rule["compiled"].search(os.path.basename(f))]
        if not bad:
            findings.append(finding("ok", "name_checks",
                                    f"{rule['label']}: 全部合规",
                                    f"检查 {len(targets)} 个文件"))
        else:
            findings.append(finding(rule["severity"], "name_checks",
                                    rule["label"], _cap_lines(bad)))
    return findings


def check_secrets(root: str, files: list, rules: dict) -> list:
    """密钥扫描: 文本文件逐行匹配; 按(标签)聚合, detail 为 "相对路径:行号"."""
    hits = {}
    for rel in files:
        if os.path.splitext(rel)[1] not in rules["scan_extensions"]:
            continue
        path = os.path.join(root, rel)
        try:
            if os.path.getsize(path) > MAX_SCAN_BYTES:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            for label, compiled, severity in rules["secret_patterns"]:
                if not compiled.search(line):
                    continue
                entry = hits.setdefault(label,
                                        {"severity": severity, "where": []})
                if _SEVERITY_ORDER[severity] > _SEVERITY_ORDER[
                        entry["severity"]]:
                    entry["severity"] = severity
                entry["where"].append(f"{rel}:{lineno}")
    return [finding(entry["severity"], "secret_scan", f"疑似{label}",
                    _cap_lines(entry["where"]))
            for label, entry in hits.items()]


def check_large(root: str, files: list, mb: int) -> list:
    """大文件警告: 超过阈值逐个 warn, 上限 MAX_LARGE_ITEMS 条."""
    if mb <= 0:
        return []
    threshold = mb * 1024 * 1024
    oversized = []
    for rel in files:
        try:
            size = os.path.getsize(os.path.join(root, rel))
        except OSError:
            continue
        if size > threshold:
            oversized.append((rel, size))
    findings = [
        finding("warn", "large_file", f"大文件 {rel}({size // 1048576} MB)",
                f"阈值 {mb} MB, 提交前确认是否需要包含")
        for rel, size in oversized[:MAX_LARGE_ITEMS]]
    if len(oversized) > MAX_LARGE_ITEMS:
        findings.append(finding(
            "warn", "large_file",
            f"另有 {len(oversized) - MAX_LARGE_ITEMS} 个大文件未逐一列出"))
    return findings


def run(job: PluginJob) -> PluginReport:
    """入口: 校验目标 -> 加载规则包 -> 五类检查 -> 汇总报告.

    目标问题(空/非目录/多目标)用 finding 表达(留痕 ok + report 记问题);
    规则包问题抛 ValueError(宿主留痕 failed).
    """
    findings = []
    if not job.targets:
        return make_report([finding("error", "input", "未给定检查目标")])
    target = job.targets[0]
    if len(job.targets) > 1:
        findings.append(finding(
            "warn", "input", "多目标仅检查第一个",
            f"忽略其余 {len(job.targets) - 1} 个目标"))
    if not os.path.isdir(target):
        findings.append(finding("error", "input", f"目标不是目录: {target}"))
        return make_report(findings)
    params = job.params
    spec = params.get("rules_path") or params.get("rules") or "basic"
    rules = load_rules(spec)   # 规则包问题抛 ValueError 交宿主留痕 failed
    findings.extend(finding("warn", "rules", text)
                    for text in rules["_warnings"])
    excludes = _excludes(params.get("exclude", ""))
    files = _walk_files(target, excludes)
    findings.extend(check_required(target, rules, excludes))
    findings.extend(check_forbidden(target, rules, excludes))
    findings.extend(check_names(rules, files))
    findings.extend(check_secrets(target, files, rules))
    findings.extend(check_large(target, files,
                                int(params.get("large_file_mb", 0))))
    if params.get("only_problems"):
        findings = [f for f in findings if f.severity != "ok"]
    errors = sum(1 for f in findings if f.severity == "error")
    warns = sum(1 for f in findings if f.severity == "warn")
    summary = (f"规则包 {rules['name']}: 检查 {len(files)} 个文件, "
               f"{errors} 项错误, {warns} 项警告")
    if not errors and not warns:
        summary += ", 全部通过"
    return make_report(findings, summary=summary)
