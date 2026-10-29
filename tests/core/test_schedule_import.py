"""schedule_import.py 测试: 个人校历 html 解析与联排课合并."""

import os
from datetime import date, time

import pytest

from shuimu.core.schedule import Event
from shuimu.core.schedule_import import merge_adjacent, parse_calendar_html

CALENDAR_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                                "portal_calendar_sample.html")

DAY = date(2026, 10, 26)


def _event(start, end, title="课", location="", day=None):
    """快速构造同一天的事件."""
    return Event(day or DAY, start, end, title, location)


class TestMergeAdjacent:
    """联排课合并规则."""

    def test_gap_15_merges(self):
        """间隔恰 15 分钟的同名课合并为一条."""
        merged = merge_adjacent([_event(time(8), time(9, 35)),
                                 _event(time(9, 50), time(11, 25), title="课")])
        assert len(merged) == 1
        assert (merged[0].start, merged[0].end) == (time(8), time(11, 25))

    def test_gap_16_not_merged(self):
        """间隔超过 15 分钟不合并."""
        merged = merge_adjacent([_event(time(8), time(9, 35)),
                                 _event(time(9, 51), time(11, 25), title="课")])
        assert len(merged) == 2

    def test_different_title_not_merged(self):
        """同时间相邻但课名不同不合并."""
        merged = merge_adjacent([_event(time(8), time(9, 35), title="a"),
                                 _event(time(9, 50), time(11, 25), title="b")])
        assert len(merged) == 2

    def test_title_only_location_ignored(self):
        """合并只看课名, 地点不同也合并(同日联排换教室)."""
        merged = merge_adjacent([_event(time(8), time(9, 35), location="一教"),
                                 _event(time(9, 50), time(11, 25), title="课",
                                        location="三教")])
        assert len(merged) == 1 and merged[0].location == "一教"

    def test_three_chain_merges(self):
        """三节连排链式合并为一条."""
        merged = merge_adjacent([_event(time(8), time(9, 35)),
                                 _event(time(9, 50), time(11, 25), title="课"),
                                 _event(time(11, 25), time(12, 10), title="课")])
        assert len(merged) == 1
        assert (merged[0].start, merged[0].end) == (time(8), time(12, 10))

    def test_unsorted_input_handled(self):
        """乱序输入按开始时间归并."""
        merged = merge_adjacent([_event(time(9, 50), time(11, 25), title="课"),
                                 _event(time(8), time(9, 35))])
        assert len(merged) == 1 and merged[0].start == time(8)


class TestCalendarHtml:
    """个人校历 HTML(展开视图)解析."""

    def test_fixture_events_and_merge(self):
        """4 行课经联排合并得 3 条事件, 合并 1 处."""
        result = parse_calendar_html(CALENDAR_FIXTURE)
        assert len(result.events) == 3 and result.merged == 1
        assert not result.warnings
        math = result.events[0]
        assert (math.date, math.start, math.end) == (
            date(2026, 10, 26), time(8), time(11, 25))  # 08:00+09:50 合并
        assert math.title == "高等数学A(1)" and math.location == "一教101"

    def test_exact_course_hours_kept(self):
        """逐次事件保留确切课时."""
        result = parse_calendar_html(CALENDAR_FIXTURE)
        ai = [e for e in result.events if "AI" in e.title][0]
        assert (ai.start, ai.end) == (time(19, 20), time(20, 55))
        assert ai.title == "AI 编程进阶(第 5 周)"  # 说明列追加

    def test_scaffold_rows_skipped(self):
        """无时间列的骨架行与表头行跳过."""
        result = parse_calendar_html(CALENDAR_FIXTURE)
        titles = [e.title for e in result.events]
        assert "无时间列的骨架行应跳过" not in titles
        assert "开始时间" not in titles

    def test_no_date_sections_raises(self, tmp_path):
        """没有日期分节时报错."""
        f = tmp_path / "x.html"
        f.write_text("<html>没有星号分节</html>", encoding="utf-8")
        with pytest.raises(ValueError):
            parse_calendar_html(str(f))

    def test_no_timed_rows_warns(self, tmp_path):
        """有分节但无带时间条目时给警告."""
        f = tmp_path / "empty.html"
        f.write_text("★2026-10-26<tr><td>x</td></tr>", encoding="utf-8")
        result = parse_calendar_html(str(f))
        assert not result.events and result.warnings

    def test_gb18030_fallback(self, tmp_path):
        """utf-8 解码失败回退 gb18030(页面声明 gb2312)."""
        content = ("★2026-10-26<tr><td>10:00</td><td>11:40</td><td>上课</td>"
                   "<td>物理实验</td><td>实验室</td><td></td></tr>")
        f = tmp_path / "gb.html"
        f.write_bytes(content.encode("gb18030"))
        result = parse_calendar_html(str(f))
        assert [e.title for e in result.events] == ["物理实验"]
