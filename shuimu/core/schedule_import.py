"""从信息门户"个人校历"页面另存的 HTML 导入课程(纯逻辑, 不依赖 Qt).

页面为服务端渲染的展开视图: "★YYYY-MM-DD" 日期分节, 节内表格每行一次
上课(开始时间|结束时间|分类|内容|地点|说明), 含确切课时与调休/调课结果.

联排课在页面里是相邻的多行(如 08:00-09:35 与 09:50-11:25), 本模块按
"同日同名且间隔不超过 15 分钟"合并为一条(阈值与 thu-info-app 的
mergeAdjacent 一致; 仅参考其公开行为, 未复制其 BSL 1.1 代码).
"""

import re
from dataclasses import dataclass, field
from datetime import date, time
from html import unescape

from shuimu.core.schedule import Event

_DATE_HEAD = re.compile(r"★\s*(\d{4}-\d{2}-\d{2})")
_TIME_CELL = re.compile(r"^(\d{1,2}):(\d{2})$")
MERGE_GAP_MIN = 15  # 同日同名条目间隔不超过该分钟数时合并


@dataclass
class ImportResult:
    """一次导入的解析结果."""

    events: list = field(default_factory=list)
    merged: int = 0  # 被合并掉的行数
    warnings: list = field(default_factory=list)


def _clean_html(fragment: str) -> str:
    """去标签, 反转义实体, 空白折叠为单个空格."""
    text = unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text).strip()


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def merge_adjacent(events: list) -> list:
    """同日同名且间隔不超过 MERGE_GAP_MIN 分钟的条目合并为一条.

    就地修改并返回入参列表(要求条目已属同一天); 三节以上连排会链式合并.
    """
    merged = []
    for entry in sorted(events, key=lambda e: (e.start, e.title)):
        previous = merged[-1] if merged else None
        if (previous is not None and previous.title == entry.title
                and 0 <= _minutes(entry.start) - _minutes(previous.end)
                <= MERGE_GAP_MIN):
            previous.end = entry.end
        else:
            merged.append(entry)
    return merged


def _parse_calendar_rows(segment: str, day: date, result: ImportResult) -> None:
    """解析一个日期节内的表格行: 带时间的行收集, 联排课合并后追加."""
    events = []
    for tr in re.finditer(r"<tr[^>]*>([\s\S]*?)</tr>", segment):
        cells = [_clean_html(cell) for cell in re.findall(
            r"<td[^>]*>([\s\S]*?)</td>", tr.group(1))]
        if len(cells) < 5:
            continue
        start_match = _TIME_CELL.match(cells[0])
        end_match = _TIME_CELL.match(cells[1])
        if not start_match or not end_match or not cells[3]:
            continue
        title = cells[3] + (f"({cells[5]})" if len(cells) > 5 and cells[5]
                            else "")
        events.append(Event(
            day,
            time(int(start_match.group(1)), int(start_match.group(2))),
            time(int(end_match.group(1)), int(end_match.group(2))),
            title, cells[4]))
    merged_events = merge_adjacent(events)
    result.events.extend(merged_events)
    result.merged += len(events) - len(merged_events)


def parse_calendar_html(path: str) -> ImportResult:
    """解析门户"个人校历"页面另存的 HTML, 每次上课解析为一条 Event.

    无时间列的行(页面骨架)跳过; 说明列非空时追加到标题括注内.
    文件编码 utf-8 失败回退 gb18030(页面声明 gb2312, 浏览器另存可能已是
    utf-8).
    """
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gb18030", errors="replace")

    result = ImportResult()
    heads = list(_DATE_HEAD.finditer(text))
    if not heads:
        raise ValueError("未找到日期分节(需含 ★YYYY-MM-DD)")
    for index, head in enumerate(heads):
        day = date.fromisoformat(head.group(1))
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        _parse_calendar_rows(text[head.end():end], day, result)
    if not result.events:
        result.warnings.append("页面不含任何带时间的日历条目")
    return result
