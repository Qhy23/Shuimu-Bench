"""清华云盘插件入口: 复用 core/cloud 协议层与 cloud_sync 编排, 按 action 分派.

主通道是 controller.cloud_* 动作(GUI 云盘页签与 CLI cloud-* 子命令);
本入口保持与通用插件框架的对接(plugin-run cloud).
"""

from shuimu.core.campus_net import TransportError, UrllibTransport
from shuimu.core.cloud import CloudClient, CloudError
from shuimu.core.cloud_sync import clamp_depth, mount_share, plan_share
from shuimu.core.paths import default_db_path
from shuimu.core.pluginapi import (
    PluginJob,
    PluginReport,
    finding,
    make_report,
)
from shuimu.core.storage import Storage


def _transport_factory():
    """生产传输层(测试可替换为 MemoryTransport)."""
    return UrllibTransport()


def _storage() -> Storage:
    """工作台数据库(测试可替换为 tmp 库)."""
    return Storage(default_db_path())


def run(job: PluginJob) -> PluginReport:
    """按 params.action 分派: browse(只读预览) / mount(挂载登记)."""
    action = job.params.get("action", "browse")
    if action == "mount":
        return _mount(job.params)
    return _browse(job.params)


def _client():
    """匿名云盘客户端."""
    return CloudClient(_transport_factory())


def _browse(params: dict) -> PluginReport:
    """浏览分享库: 目录与课程匹配预览(不登记)."""
    url = str(params.get("url", "")).strip()
    storage = _storage()
    try:
        try:
            outcome = plan_share(storage, _client(), url,
                                 clamp_depth(params.get("depth", 2)))
        except CloudError as exc:
            return make_report(
                [finding("error", "share", str(exc))], summary=str(exc))
        except TransportError as exc:
            return make_report(
                [finding("error", "network", f"网络异常: {exc}")],
                summary="暂时无法访问")
        findings = [
            finding("ok", "mount", f"{m.name} -> {m.course_name}", m.url)
            for m in outcome.mounts]
        if outcome.unmatched:
            findings.append(finding(
                "warn", "unmatched",
                f"{len(outcome.unmatched)} 个目录未匹配到课程",
                ", ".join(outcome.unmatched[:20])))
        return make_report(findings, summary=outcome.summary())
    finally:
        storage.close()


def _mount(params: dict) -> PluginReport:
    """挂载分享库: 匹配目录登记为课程链接资源."""
    url = str(params.get("url", "")).strip()
    storage = _storage()
    try:
        try:
            outcome = mount_share(storage, _client(), url,
                                  clamp_depth(params.get("depth", 2)))
        except CloudError as exc:
            return make_report(
                [finding("error", "share", str(exc))], summary=str(exc))
        except TransportError as exc:
            return make_report(
                [finding("error", "network", f"网络异常: {exc}")],
                summary="暂时无法访问")
        findings = [
            finding("ok", "mount", f"已挂载 {d} -> {c}",
                    f"资源 #{r}")
            for c, d, _u, r in outcome.mounted]
        for c, d in outcome.skipped:
            findings.append(finding("ok", "mount", f"已挂载过 {d} -> {c}"))
        if outcome.unmatched:
            findings.append(finding(
                "warn", "unmatched",
                f"{len(outcome.unmatched)} 个目录未匹配到课程",
                ", ".join(outcome.unmatched[:20])))
        return make_report(findings, summary=outcome.summary())
    finally:
        storage.close()
