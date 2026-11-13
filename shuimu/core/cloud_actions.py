"""云盘业务动作: Controller 的混入部分(门禁/浏览/挂载).

依赖宿主属性与方法: storage, plugins, _campus_transport_factory
(通用传输层工厂, 云盘匿名访问无需会话). 动作语义与留痕约定同
controller 本体: 浏览只读不留痕, 挂载登记资源并留痕.
"""

from shuimu.core.actions import ActionResult
from shuimu.core.campus_net import TransportError
from shuimu.core.cloud import CloudClient, CloudError, parse_share_url
from shuimu.core.cloud_sync import clamp_depth, mount_share, plan_share


class CloudActions:
    """云盘动作集合(由 Controller 继承)."""

    def _cloud_gate(self):
        """云盘插件门禁: 未启用/不可用时返回失败结果(零联网), 可用为 None."""
        record = self.plugins.get("cloud")
        if record is None:
            return ActionResult(False, "云盘插件未安装")
        if record.status == "broken":
            return ActionResult(False, f"云盘插件损坏: {record.error}")
        if record.status == "disabled":
            return ActionResult(False, "云盘插件未启用(需网络授权)")
        return None

    def cloud_browse(self, url: str, max_depth: int = 2) -> ActionResult:
        """浏览分享库: 列目录并按课程名匹配(只读, 不留痕)."""
        gate = self._cloud_gate()
        if gate is not None:
            return gate
        try:
            ref = parse_share_url(url)
        except CloudError as exc:
            return ActionResult(False, str(exc))
        client = CloudClient(self._campus_transport_factory())
        try:
            outcome = plan_share(self.storage, client, ref.root_url,
                                 clamp_depth(max_depth))
        except TransportError as exc:
            return ActionResult(False, f"暂时无法访问(网络异常): {exc}",
                                {"state": "network_error"})
        except CloudError as exc:
            return ActionResult(False, str(exc), {"state": "cloud_error"})
        detail = {
            "share": outcome.share_title,
            "token": outcome.share.token,
            "mounts": [{"path": m.path, "name": m.name, "url": m.url,
                        "course_id": m.course_id,
                        "course_name": m.course_name}
                       for m in outcome.mounts],
            "unmatched": list(outcome.unmatched),
        }
        return ActionResult(True, outcome.summary(), detail)

    def cloud_mount(self, url: str, max_depth: int = 2) -> ActionResult:
        """挂载分享库: 匹配目录登记为课程链接资源, 留痕."""
        gate = self._cloud_gate()
        if gate is not None:
            return gate
        try:
            ref = parse_share_url(url)
        except CloudError as exc:
            return ActionResult(False, str(exc))
        client = CloudClient(self._campus_transport_factory())
        try:
            outcome = mount_share(self.storage, client, ref.root_url,
                                  clamp_depth(max_depth))
        except TransportError as exc:
            return ActionResult(False, f"暂时无法访问(网络异常): {exc}",
                                {"state": "network_error"})
        except CloudError as exc:
            return ActionResult(False, str(exc), {"state": "cloud_error"})
        detail = {
            "share": outcome.share_title,
            "token": outcome.share.token,
            "depth": clamp_depth(max_depth),
            "mounted": [{"course": c, "dir": d, "url": u, "resource_id": r}
                        for c, d, u, r in outcome.mounted],
            "skipped": [list(item) for item in outcome.skipped],
            "unmatched": list(outcome.unmatched),
        }
        self.storage.log_operation(
            "cloud_mount", params={"url": outcome.share.root_url,
                                   "depth": clamp_depth(max_depth)},
            detail=detail)
        return ActionResult(True, outcome.summary(), detail)
