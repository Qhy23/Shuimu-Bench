"""云盘资料库挂载编排: 目录树收集, 课程名匹配, 引用式登记.

controller(留痕动作)与插件壳共用的业务层: 协议在 core/cloud.py,
存取在 core/storage.py, 本模块负责一次浏览/挂载事务的顺序与汇总.
不整库下载: 只把匹配课程的子目录链接登记为 kind=link 资源.
"""

import sqlite3
from dataclasses import dataclass, field

from shuimu.core.cloud import (
    CloudClient,
    ShareRef,
    parse_share_url,
    subdir_url,
)

# 匹配搜索深度边界(防失控遍历)
MIN_DEPTH = 1
MAX_DEPTH = 3


@dataclass
class CloudMountPlan:
    """一个待挂载目录: 云盘子目录 + 匹配到的本地课程."""

    path: str            # 云盘目录路径, 如 /专业核心课程/核仪器原理/
    name: str            # 目录名(作为资源显示名)
    url: str             # 子目录网页地址
    course_id: int
    course_name: str


@dataclass
class CloudPlanOutcome:
    """一次浏览(不登记)的汇总."""

    share: ShareRef
    share_title: str = ""
    mounts: list = field(default_factory=list)    # [CloudMountPlan]
    unmatched: list = field(default_factory=list)  # [目录名]

    def summary(self) -> str:
        """单行中文汇总."""
        title = self.share_title or "云盘资料库"
        return (f"资料库 «{title}»: {self.dir_count()} 个目录,"
                f" 匹配到课程 {len(self.mounts)} 个")

    def dir_count(self) -> int:
        """目录总数(含未匹配)."""
        return len(self.mounts) + len(self.unmatched)


@dataclass
class CloudMountOutcome:
    """一次挂载(登记)的汇总."""

    share: ShareRef
    share_title: str = ""
    mounted: list = field(default_factory=list)
    # [(course_name, dir_name, url, resource_id)]
    skipped: list = field(default_factory=list)    # 已登记过 [(课程名, 目录名)]
    unmatched: list = field(default_factory=list)  # [目录名]

    def summary(self) -> str:
        """单行中文汇总."""
        title = self.share_title or "云盘资料库"
        return (f"资料库 «{title}»: 新挂载 {len(self.mounted)},"
                f" 已挂载 {len(self.skipped)}, 未匹配 {len(self.unmatched)}")


def clamp_depth(max_depth) -> int:
    """深度收敛到 [MIN_DEPTH, MAX_DEPTH]; 非整数按默认 2."""
    try:
        value = int(max_depth)
    except (TypeError, ValueError):
        return 2
    return max(MIN_DEPTH, min(MAX_DEPTH, value))


# 匹配规则序: 课名全等 > 课名前缀互含 > 课名互含; 目录名只做 strip,
# 不强制建课(由用户在课程空间自行维护).
_MATCH_RULES = (
    lambda name, local: name == local,
    lambda name, local: name.startswith(local) or local.startswith(name),
    lambda name, local: name in local or local in name,
)


def match_course(dir_name: str, local: dict):
    """云盘目录名 -> 本地课程 id; 不匹配返回 None."""
    name = (dir_name or "").strip()
    for rule in _MATCH_RULES:
        for cid, value in local.items():
            local_name = (value or "").strip()
            if name and local_name and rule(name, local_name):
                return cid
    return None


def plan_share(storage, client: CloudClient, url: str,
               max_depth: int = 2) -> CloudPlanOutcome:
    """浏览分享库: 解析链接 -> 收集目录 -> 匹配课程(零登记)."""
    ref = parse_share_url(url)
    depth = clamp_depth(max_depth)
    title = client.share_title(ref.token)
    dirs = client.walk_dirs(ref.token, ref.path, depth)
    local = {row["id"]: row["name"] for row in storage.list_courses()}
    outcome = CloudPlanOutcome(share=ref, share_title=title)
    for item in dirs:
        course_id = match_course(item.name, local)
        if course_id is None:
            outcome.unmatched.append(item.name)
            continue
        outcome.mounts.append(CloudMountPlan(
            path=item.path, name=item.name,
            url=subdir_url(ref.token, item.path),
            course_id=course_id, course_name=local[course_id]))
    return outcome


def mount_share(storage, client: CloudClient, url: str,
                max_depth: int = 2) -> CloudMountOutcome:
    """挂载分享库: 浏览 + 把匹配目录登记为课程的链接资源(引用式).

    同一链接在同一课程下重复挂载因 UNIQUE(course_id, path_or_url)
    冲突转为 skipped, 动作幂等.
    """
    planned = plan_share(storage, client, url, max_depth)
    outcome = CloudMountOutcome(
        share=planned.share, share_title=planned.share_title,
        unmatched=list(planned.unmatched))
    for plan in planned.mounts:
        try:
            rid = storage.add_resource(
                plan.course_id, "link", plan.url,
                display_name=plan.name)
        except sqlite3.IntegrityError:
            outcome.skipped.append((plan.course_name, plan.name))
            continue
        outcome.mounted.append(
            (plan.course_name, plan.name, plan.url, rid))
    return outcome
