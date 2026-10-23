"""从清华大学信息门户导出的课表 .xls 导入(纯逻辑, 不依赖 Qt).

门户"个人课表"导出为 BIFF 二进制 .xls(report 工作表):
  - 某行为星期表头(星期一..星期日), 其下"第N节"行构成 6 大节网格
  - 常规课单元格: "课程名(教师;属性;周次;地点)", 周次如 全周/前八周/5-12周
  - 实验课单元格可含多条(\\r\\n 分隔): "实验名(地点;第N周)" 单周次
  - 网格后为"未安排时间课程"明细区(无时间, 不导入只计数)

时段映射与 thu-info-app 项目总结的 6 大节标准一致(仅参考其公开协议
格式, 未复制其代码, 该项目为 BSL 1.1 许可).
"""

import re
from dataclasses import dataclass, field
from datetime import time

import xlrd

from .schedule import TimetableSlot

# 6 大节 -> (开始, 结束)
SESSION_TIMES = {
    1: (time(8, 0), time(9, 35)),
    2: (time(9, 50), time(12, 15)),
    3: (time(13, 30), time(15, 5)),
    4: (time(15, 20), time(16, 55)),
    5: (time(17, 5), time(18, 40)),
    6: (time(19, 20), time(21, 45)),
}

WEEKDAY_OF_HEADER = {"星期一": 1, "星期二": 2, "星期三": 3, "星期四": 4,
                     "星期五": 5, "星期六": 6, "星期日": 7}

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_WEEK_FIELD = re.compile(r"^(第\d+周|\d+\s*[-–—至]\s*\d+\s*周|[前后]8?八?周|全周|单周|双周)$")


@dataclass
class ImportResult:
    """一次导入的解析结果."""

    slots: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unscheduled: int = 0  # 未安排时间课程条数(不导入)


def parse_week_spec(text: str):
    """周次字段转 weeks 区间. 返回 ((起, 止) 或 None, 警告或 None).

    None 表示不过滤(每周显示). 无法识别抛 ValueError; 单周/双周无法用
    区间表达, 降级为每周并给出警告.
    """
    text = text.strip()
    if text == "全周":
        return None, None
    if text in ("前八周", "前8周"):
        return (1, 8), None
    if text in ("后八周", "后8周"):
        return (9, 16), None  # 按标准 16 周学期
    if text in ("单周", "双周"):
        return None, f"\"{text}\"无法按区间表达, 已按每周导入"
    match = re.fullmatch(r"第(\d+)周", text)
    if match:
        week = int(match.group(1))
        return (week, week), None
    match = re.fullmatch(r"(\d+)\s*[-–—至]\s*(\d+)\s*周", text)
    if match:
        begin, end = int(match.group(1)), int(match.group(2))
        if begin > end:
            raise ValueError(f"周次区间起大于止: {text}")
        return (begin, end), None
    raise ValueError(f"无法识别的周次: {text}")


def _parse_entry(text: str, weekday: int, session: int):
    """解析单元格内一条课程文本, 返回 TimetableSlot; 非法抛 ValueError."""
    text = text.strip()
    if "(" not in text or not text.endswith(")"):
        raise ValueError(f"缺少括注字段: {text}")
    title, _, tail = text.rpartition("(")
    title = title.strip()
    if not title:
        raise ValueError(f"缺少课程名: {text}")
    fields = [f.strip() for f in re.split(r"[；;]", tail[:-1]) if f.strip()]
    week_idx = next((i for i, f in enumerate(fields) if _WEEK_FIELD.match(f)),
                    None)
    if week_idx is None:
        raise ValueError(f"缺少周次字段: {text}")
    weeks, warning = parse_week_spec(fields[week_idx])
    # 周次字段之后是地点(常规课); 之前是地点(实验课"地点;第N周"格式)
    rest = fields[week_idx + 1:] or fields[:week_idx]
    location = "；".join(rest)
    start, end = SESSION_TIMES[session]
    slot = TimetableSlot(weekday, start, end, title, location, weeks)
    return slot, warning


def _find_weekday_header(sheet):
    """定位星期表头行, 返回 (列->weekday 映射, 行号); 未找到返回 ({}, -1)."""
    for row in range(sheet.nrows):
        mapping = {}
        for col in range(sheet.ncols):
            value = sheet.cell_value(row, col)
            if isinstance(value, str) and value.strip() in WEEKDAY_OF_HEADER:
                mapping[col] = WEEKDAY_OF_HEADER[value.strip()]
        if len(mapping) >= 5:
            return mapping, row
    return {}, -1


def _is_unscheduled_marker(sheet, row) -> bool:
    """该行是否为"未安排时间课程"分节标记."""
    for col in range(sheet.ncols):
        value = sheet.cell_value(row, col)
        if isinstance(value, str) and "未安排时间课程" in value:
            return True
    return False


def _scan_unscheduled_count(sheet, start_row) -> int:
    """统计明细区数据行数(跳过表头行)."""
    count = 0
    header_seen = False
    for row in range(start_row, sheet.nrows):
        first = str(sheet.cell_value(row, 0)).strip()
        is_header = first == "课程号"
        header_seen = header_seen or is_header
        if header_seen and not is_header and first:
            count += 1
    return count


def _scan_session_row(sheet, row, weekday_of_col, result: ImportResult) -> None:
    """扫描一个"第N节"行: 各星期列单元格按换行拆条解析进 result."""
    first = str(sheet.cell_value(row, 0)).strip()
    match = re.fullmatch(r"第(\d+)节", first)
    if not match:
        return
    session = int(match.group(1))
    if session not in SESSION_TIMES:
        result.warnings.append(f"第 {session} 节超出 1-6 范围, 该行跳过")
        return
    for col, weekday in sorted(weekday_of_col.items()):
        value = sheet.cell_value(row, col)
        if not isinstance(value, str) or not value.strip():
            continue
        for entry in re.split(r"[\r\n]+", value):
            if not entry.strip():
                continue
            try:
                slot, warning = _parse_entry(entry.strip(), weekday, session)
            except ValueError as exc:
                result.warnings.append(
                    f"第{session}节 {WEEKDAY_NAMES[weekday - 1]}列跳过: {exc}")
                continue
            if warning:
                result.warnings.append(f"{slot.title}: {warning}")
            result.slots.append(slot)


def parse_portal_xls(path: str) -> ImportResult:
    """解析门户导出的课表 .xls, 返回 slots/warnings/未安排计数.

    结构扫描而非硬编码坐标: 先找星期表头行(>=5 个 星期X 单元格), 其下
    首列为"第N节"的行是时段行; 出现"未安排时间课程"后进入明细区.
    """
    book = xlrd.open_workbook(path)
    sheet = book.sheet_by_index(0)
    weekday_of_col, header_row = _find_weekday_header(sheet)
    if not weekday_of_col:
        raise ValueError("未找到星期表头行(需含 星期一..星期日)")

    result = ImportResult()
    details_row = sheet.nrows
    for row in range(header_row + 1, sheet.nrows):
        if _is_unscheduled_marker(sheet, row):
            details_row = row
            break
        _scan_session_row(sheet, row, weekday_of_col, result)
    result.unscheduled = _scan_unscheduled_count(sheet, details_row + 1)
    return result
