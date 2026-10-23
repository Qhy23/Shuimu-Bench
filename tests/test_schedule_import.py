"""schedule_import.py 测试: 周次解析, 条目解析, 结构扫描与真实二进制 fixture."""

import os
from datetime import time

import pytest

from shuimu.schedule_import import (
    SESSION_TIMES,
    parse_portal_xls,
    parse_week_spec,
)
from shuimu.schedule import TimetableSlot

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "portal_sample.xls")


class TestWeekSpec:
    """周次字段解析."""

    def test_keywords(self):
        """全周/前后八周/单双周."""
        assert parse_week_spec("全周") == (None, None)
        assert parse_week_spec("前八周") == ((1, 8), None)
        assert parse_week_spec("后8周") == ((9, 16), None)
        weeks, warning = parse_week_spec("单周")
        assert weeks is None and warning

    def test_ranges(self):
        """区间与单周次."""
        assert parse_week_spec("5-12周") == ((5, 12), None)
        assert parse_week_spec("第4周") == ((4, 4), None)

    def test_invalid(self):
        """无法识别与起大于止抛 ValueError."""
        with pytest.raises(ValueError):
            parse_week_spec("有时上")
        with pytest.raises(ValueError):
            parse_week_spec("9-3周")


class TestFixtureIntegration:
    """真实二进制 fixture(合成, 与门户导出同构)."""

    def _find(self, title):
        slots = parse_portal_xls(FIXTURE).slots
        return [s for s in slots if s.title == title]

    def test_counts(self):
        """正常课 + 一格双实验共 8 条, 未安排 2 门."""
        result = parse_portal_xls(FIXTURE)
        assert len(result.slots) == 8
        assert result.unscheduled == 2

    def test_regular_course(self):
        """常规课: 名称/地点/周次/时段映射正确."""
        slots = self._find("高等数学A")
        assert len(slots) == 1
        slot = slots[0]
        assert slot.weekday == 1 and slot.location == "一教101"
        assert slot.weeks is None  # 全周不过滤
        assert (slot.start, slot.end) == SESSION_TIMES[1]

    def test_week_ranges(self):
        """区间周次与前八周."""
        assert self._find("大学物理")[0].weeks == (5, 12)
        assert self._find("程序设计")[0].weeks == (1, 8)

    def test_single_week_experiments(self):
        """一格双实验按 \\r\\n 拆分, 各自单周次, 地点在周次之前."""
        exp_a = self._find("实验A- subgroup")
        exp_b = self._find("实验B- subgroup")
        assert exp_a[0].weeks == (4, 4) and exp_a[0].location == "老工物馆137房间"
        assert exp_b[0].weeks == (5, 5) and exp_b[0].location == "工物馆120"
        assert exp_a[0].weekday == 1 and exp_a[0].start == time(19, 20)

    def test_odd_even_week_fallback_warning(self):
        """单周/双周降级为每周并记警告."""
        titles = {w.split(":")[0] for w in parse_portal_xls(FIXTURE).warnings}
        assert "人工智能导论" in titles and "英语" in titles
        assert self._find("人工智能导论")[0].weeks is None

    def test_real_course_shape(self):
        """与真实门户导出同构的 AI 编程课条目."""
        slot = self._find("AI编程进阶：方法与实践")[0]
        assert slot.weekday == 2 and slot.weeks == (5, 12)
        assert slot.location == "建华/经管新楼A312"
        assert (slot.start, slot.end) == SESSION_TIMES[6]


class TestStructureScan:
    """结构扫描与容错(打桩 xlrd)."""

    @pytest.fixture()
    def matrix_book(self, monkeypatch):
        """用内存矩阵替换 xlrd.open_workbook."""
        def install(matrix):
            """把后续 parse_portal_xls 调用指向内存矩阵."""

            class Sheet:
                """与 xlrd Sheet 同构的内存替身."""

                name, nrows, ncols = "report", len(matrix), max(map(len, matrix))

                @staticmethod
                def cell_value(row, col):
                    """按坐标取单元格, 越界返回空串."""
                    row_data = matrix[row]
                    return row_data[col] if col < len(row_data) else ""

            class Book:
                """与 xlrd Book 同构的内存替身."""

                @staticmethod
                def sheet_by_index(_index):
                    """返回唯一的 Sheet."""
                    return Sheet()

            monkeypatch.setattr("shuimu.schedule_import.xlrd.open_workbook",
                                lambda _path: Book())
        return install

    def test_no_weekday_header(self, matrix_book):
        """没有星期表头行时报错."""
        matrix_book([["第1节", "课"]])
        with pytest.raises(ValueError):
            parse_portal_xls("fake.xls")

    def test_header_not_first_row(self, matrix_book):
        """标题行在前, 表头行靠结构定位."""
        matrix_book([
            ["任意标题"],
            ["", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
            ["第1节", "课A(师；必修；全周；地)"],
        ])
        result = parse_portal_xls("fake.xls")
        assert [s.title for s in result.slots] == ["课A"]

    def test_malformed_entries_skipped(self, matrix_book):
        """无括注/无周次/空名的条目跳过并记警告."""
        matrix_book([
            ["", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
            ["第1节", "没有括注的课", "缺周次(师；必修；一教)", "(师；必修；全周；地)"],
        ])
        result = parse_portal_xls("fake.xls")
        assert result.slots == []
        assert len(result.warnings) == 3

    def test_session_out_of_range(self, matrix_book):
        """第 7 节超出范围整行跳过."""
        matrix_book([
            ["", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
            ["第7节", "课A(师；必修；全周；地)"],
        ])
        result = parse_portal_xls("fake.xls")
        assert not result.slots and len(result.warnings) == 1


def test_slot_shape():
    """解析产物字段类型与 schedule 模型一致."""
    result = parse_portal_xls(FIXTURE)
    slot = result.slots[0]
    assert isinstance(slot, TimetableSlot)
    assert isinstance(slot.start, time) and isinstance(slot.end, time)
