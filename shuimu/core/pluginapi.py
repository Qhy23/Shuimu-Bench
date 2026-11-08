"""插件 API v1: 宿主与插件共享的数据契约. 仅标准库, 不依赖 Qt.

插件入口模块(manifest.entry, 缺省 plugin 即 <插件目录>/plugin.py)须暴露:

    def run(job: PluginJob) -> PluginReport

宿主经 core.pluginman 调度; run 抛出的任意异常都会被捕获并留痕
status=failed, 不会崩掉宿主. 插件不应自行打印/弹窗, 结论一律写进 report.

设计约束(为将来进程外隔离留缝, 勿破坏): 本模块的数据类保持纯数据
(无 Qt 对象/回调/文件句柄), 天然可序列化.
"""

from dataclasses import dataclass, field

SEVERITIES = ("ok", "warn", "error")
SEVERITY_LABEL = {"ok": "通过", "warn": "警告", "error": "错误"}


@dataclass
class PluginJob:
    """一次插件调用的输入.

    targets 为目标路径列表(GUI 选中或 CLI --path); params 为经 manifest
    声明校验并填充默认值后的参数, 只含最终生效键.
    """

    targets: list = field(default_factory=list)
    params: dict = field(default_factory=dict)


@dataclass
class PluginFinding:
    """一条检查/处理结论.

    severity 为 SEVERITIES 之一; check 为检查项标识(如 required_files/
    secret_scan/input); title 为一句话结论; detail 为补充说明(命中文件
    列表等, 纯文本).
    """

    severity: str
    check: str
    title: str
    detail: str = ""


@dataclass
class PluginReport:
    """一次插件调用的输出. ok = 无 error 级 finding.

    outputs 为产出文件路径列表(归档引用, 由宿主留痕).
    """

    ok: bool
    summary: str
    findings: list = field(default_factory=list)
    outputs: list = field(default_factory=list)


def finding(severity: str, check: str, title: str,
            detail: str = "") -> PluginFinding:
    """构造 PluginFinding; severity 非法立即抛 ValueError(插件作者早暴露)."""
    if severity not in SEVERITIES:
        raise ValueError(
            f"非法严重度: {severity}(须为 {'/'.join(SEVERITIES)})")
    return PluginFinding(severity, check, title, detail)


def make_report(findings: list, summary: str = None,
                outputs: list = None) -> PluginReport:
    """按 findings 汇总: ok = 无 error; summary 缺省为计数式摘要."""
    counts = {sev: sum(1 for f in findings if f.severity == sev)
              for sev in SEVERITIES}
    if summary is None:
        summary = (f"{counts['error']} 项错误, {counts['warn']} 项警告, "
                   f"{counts['ok']} 项通过")
        if not any(counts.values()):
            summary = "无检查项"
        elif counts["error"] == 0 and counts["warn"] == 0:
            summary += ", 全部通过"
    return PluginReport(not counts["error"], summary,
                        list(findings), list(outputs or []))
