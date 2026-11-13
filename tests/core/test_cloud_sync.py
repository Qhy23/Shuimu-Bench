"""cloud_sync.py 单元测试: 课程匹配与挂载编排(假 transport, tmp 库)."""

import pytest

from shuimu.core.campus_net import MemoryTransport
from shuimu.core.cloud import CloudClient, CloudError, subdir_url
from shuimu.core.cloud_sync import (
    clamp_depth,
    match_course,
    mount_share,
    plan_share,
)
from shuimu.core.storage import Storage
from tests.core.test_cloud import (
    TOKEN,
    SHARE_URL,
    add_dirents,
    add_share_page,
    dir_row,
)

# 两级资料库: 根 -> 类别 -> 课程
TREE = {
    "/": [dir_row("专业核心课程"), dir_row("其他")],
    "/专业核心课程/": [dir_row("核仪器原理", parent="/专业核心课程/"),
                 dir_row("决策方法论", parent="/专业核心课程/")],
    "/其他/": [dir_row("杂项", parent="/其他/")],
}


def seed_tree(transport):
    """登记两级目录树路由."""
    add_share_page(transport, title="多课程资料库")
    for path, rows in TREE.items():
        add_dirents(transport, rows, path=None if path == "/" else path)


def seed_local(storage):
    """预置本地课程: 一门与目录同名, 一门前缀相关."""
    exact = storage.add_course("核仪器原理")
    prefix = storage.add_course("决策方法论")
    return exact, prefix


class TestClampDepth:
    """深度收敛."""

    def test_default_and_bounds(self):
        """默认 2, 边界 1..3."""
        assert clamp_depth(None) == 2
        assert clamp_depth("bad") == 2
        assert clamp_depth(0) == 1
        assert clamp_depth(99) == 3
        assert clamp_depth(1) == 1


class TestMatchCourse:
    """目录名 -> 课程三级匹配."""

    LOCAL = {1: "核仪器原理", 2: "决策方法论", 3: "外语"}

    def test_exact(self):
        """全等优先."""
        assert match_course("核仪器原理", self.LOCAL) == 1

    def test_prefix_either_way(self):
        """前缀互含: 目录带后缀/课程带后缀."""
        assert match_course("核仪器原理(2026)", self.LOCAL) == 1
        assert match_course("决策方法", self.LOCAL) == 2

    def test_containment(self):
        """互含兜底."""
        assert match_course("大学外语", self.LOCAL) == 3

    def test_no_match(self):
        """不匹配返回 None."""
        assert match_course("完全不相关", self.LOCAL) is None

    def test_empty_name(self):
        """空目录名不匹配."""
        assert match_course("  ", self.LOCAL) is None


class TestPlanShare:
    """浏览: 目录收集 + 课程匹配(零登记)."""

    def test_plan_counts(self, tmp_path):
        """两级树: 5 目录, 2 匹配, 3 未匹配."""
        storage = Storage(str(tmp_path / "p.db"))
        try:
            seed_local(storage)
            transport = MemoryTransport()
            seed_tree(transport)
            outcome = plan_share(storage, CloudClient(transport), SHARE_URL)
            assert outcome.share.token == TOKEN
            assert outcome.share_title == "多课程资料库"
            assert outcome.dir_count() == 5
            assert {m.name for m in outcome.mounts} == \
                {"核仪器原理", "决策方法论"}
            assert all(m.course_name == m.name for m in outcome.mounts)
            assert {m.url for m in outcome.mounts} == {
                subdir_url(TOKEN, f"/专业核心课程/{m}/")
                for m in ("核仪器原理", "决策方法论")}
            assert len(outcome.unmatched) == 3
            # 未登记
            for course in storage.list_courses():
                assert storage.list_resources(course["id"]) == []
        finally:
            storage.close()

    def test_depth_one_only_root_children(self, tmp_path):
        """深度 1: 只匹配根下目录."""
        storage = Storage(str(tmp_path / "p.db"))
        try:
            storage.add_course("专业核心课程")
            transport = MemoryTransport()
            seed_tree(transport)
            outcome = plan_share(storage, CloudClient(transport),
                                 SHARE_URL, max_depth=1)
            assert [m.name for m in outcome.mounts] == ["专业核心课程"]
        finally:
            storage.close()

    def test_invalid_url_raises(self, tmp_path):
        """非法链接: 解析失败, 零请求."""
        storage = Storage(str(tmp_path / "p.db"))
        try:
            transport = MemoryTransport()
            with pytest.raises(CloudError, match="不是清华云盘"):
                plan_share(storage, CloudClient(transport), "https://x/")
            assert not transport.requests
        finally:
            storage.close()


class TestMountShare:
    """挂载: 引用式登记与幂等."""

    def _mount(self, storage, transport):
        return mount_share(storage, CloudClient(transport), SHARE_URL)

    def test_mount_registers_link_resources(self, tmp_path):
        """匹配目录登记为 link 资源(显示名为目录名)."""
        storage = Storage(str(tmp_path / "m.db"))
        try:
            exact, prefix = seed_local(storage)
            transport = MemoryTransport()
            seed_tree(transport)
            outcome = self._mount(storage, transport)
            assert len(outcome.mounted) == 2
            assert sorted(c for c, _d, _u, _r in outcome.mounted) == \
                ["决策方法论", "核仪器原理"]
            resources = storage.list_resources(exact)
            assert len(resources) == 1
            assert resources[0]["kind"] == "link"
            assert resources[0]["display_name"] == "核仪器原理"
            assert resources[0]["path_or_url"].startswith(SHARE_URL)
            assert len(storage.list_resources(prefix)) == 1
            # unmatched 原样透出
            assert len(outcome.unmatched) == 3
        finally:
            storage.close()

    def test_mount_idempotent(self, tmp_path):
        """重复挂载: UNIQUE 冲突转 skipped, 不重复登记."""
        storage = Storage(str(tmp_path / "m.db"))
        try:
            seed_local(storage)
            transport = MemoryTransport()
            seed_tree(transport)
            first = self._mount(storage, transport)
            assert len(first.mounted) == 2
            assert not first.skipped
            # 第二轮: 换一套全新路由(MemoryTransport 按序消费后会复用末条,
            # 这里重新 seed 保证响应稳定)
            transport2 = MemoryTransport()
            seed_tree(transport2)
            second = self._mount(storage, transport2)
            assert not second.mounted
            assert sorted(d for _c, d in second.skipped) == \
                ["决策方法论", "核仪器原理"]
            for course in storage.list_courses():
                assert len(storage.list_resources(course["id"])) <= 1
        finally:
            storage.close()

    def test_mount_summary(self, tmp_path):
        """汇总计数."""
        storage = Storage(str(tmp_path / "m.db"))
        try:
            seed_local(storage)
            transport = MemoryTransport()
            seed_tree(transport)
            outcome = self._mount(storage, transport)
            assert "新挂载 2" in outcome.summary()
            assert "未匹配 3" in outcome.summary()
        finally:
            storage.close()
