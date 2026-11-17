"""课程资料 -> 模型上下文: 资源枚举 + 文本提取 + 预算截断.

纯函数集合, 零 Qt 零网络; 提取器按扩展名分派. pypdf 的导入放在 PDF
提取函数内: 未安装 pypdf 只影响 PDF 路径(返回明确错误), 其余格式与
模块导入不受影响. docx/pptx 用标准库 zipfile + ElementTree 粗提
(w:t / a:t 文本节点), 不引第三方依赖.
"""

import html
import os
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

MAX_READ_BYTES = 2 * 1024 * 1024
_EXTRACT_LIMIT = 4 * 1024 * 1024   # 提取层内定上限; 真正截断在 build_context 预算层
TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".rst", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".toml", ".ini", ".log", ".py", ".c", ".cpp", ".h", ".hpp", ".java",
    ".js", ".ts", ".css", ".sh", ".sql", ".tex",
})
HTML_EXTENSIONS = frozenset({".html", ".htm"})

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


@dataclass
class ContextItem:
    """一个资料条目的纳入结果(UI 逐条展示)."""

    source: str            # 来源标签: 课程名 / "学堂缓存: <课名>" / "资源"
    kind: str              # file/link/learn_notice/learn_homework
    label: str             # 文件名/公告标题
    chars: int = 0         # 实际纳入字符数(跳过为 0)
    truncated: bool = False
    skipped: str = ""      # 非空 = 未纳入原因


@dataclass
class ContextResult:
    """build_context 的结果: text 给模型, items 给 UI."""

    text: str = ""
    items: list = field(default_factory=list)
    total_chars: int = 0   # 最终纳入总字符(<= budget)


def html_to_text(markup: str) -> str:
    """HTML -> 纯文本.

    去 script/style 块; br 与 p/div/li/tr/标题/表格结尾换行; li 前缀
    "- "; 去其余标签; 实体反解; 空行全部去除(对模型无信息量且省字符).
    学堂公告/作业描述为富文本编辑器产物(无脚本), re 方案足够.
    """
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", markup)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|table|blockquote)>", "\n",
                  text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def extract_text(path: str, limit_chars: int,
                 max_pdf_pages: int = 30) -> tuple:
    """磁盘文件 -> (text, error); error 非空 = 整文件失败(text 为空串).

    成功时 text 长度 <= limit_chars. 不支持的格式/文件不存在/过大/
    加密 PDF/无文本层均以 error 说明, 不抛异常.
    """
    if not os.path.exists(path):
        return "", "文件不存在"
    try:
        size = os.path.getsize(path)
    except OSError:
        return "", "无法读取文件"
    if size > MAX_READ_BYTES:
        return "", f"文件过大(>{MAX_READ_BYTES // (1024 * 1024)}MB)"
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return _extract_plain(path, limit_chars)
    if ext in HTML_EXTENSIONS:
        text, error = _extract_plain(path, limit_chars * 2)
        return (html_to_text(text)[:limit_chars], "") if not error else error
    if ext == ".pdf":
        return _extract_pdf(path, limit_chars, max_pdf_pages)
    if ext == ".docx":
        return _extract_docx(path, limit_chars)
    if ext == ".pptx":
        return _extract_pptx(path, limit_chars)
    return "", f"不支持的格式({ext or '无扩展名'})"


def build_context(storage, course_ids=None, resource_ids=None,
                  include_learn=False, budget: int = 60000,
                  per_file_limit: int = 20000,
                  max_pdf_pages: int = 30) -> ContextResult:
    """把勾选的课程资源与学堂缓存拼装为模型上下文.

    枚举顺序: resource_ids 显式条目 -> course_ids 各课资源(登记序,
    kind=dir 历史行跳过) -> 学堂缓存(关联课程的公告与作业描述).
    预算先到先得: 每条目上限 min(per_file_limit, 剩余总预算), 预算
    满后余条目 skipped"总预算已满"(仍列名告知).
    """
    entries = _collect_entries(storage, course_ids, resource_ids,
                               include_learn, max_pdf_pages)
    items = []
    chunks = []
    remaining = budget
    for source, kind, label, text, error in entries:
        if error:
            items.append(ContextItem(source=source, kind=kind, label=label,
                                     skipped=error))
            chunks.append(f"=== 资料: {label}(来源: {source}; 跳过: {error})"
                          " ===")
            continue
        if remaining <= 0:
            items.append(ContextItem(source=source, kind=kind, label=label,
                                     skipped="总预算已满"))
            continue
        if len(text) > min(per_file_limit, remaining):
            text = text[:min(per_file_limit, remaining)]
            item = ContextItem(source=source, kind=kind, label=label,
                               chars=len(text), truncated=True)
        else:
            item = ContextItem(source=source, kind=kind, label=label,
                               chars=len(text))
        items.append(item)
        chunks.append(f"=== 资料: {label}(来源: {source}) ===\n{text}")
        remaining -= len(text)
    body = "\n\n".join(chunks)
    return ContextResult(text=body, items=items,
                         total_chars=budget - remaining)


def _collect_entries(storage, course_ids, resource_ids,
                     include_learn, max_pdf_pages) -> list:
    """枚举全部条目 -> [(source, kind, label, text, error), ...].

    text/error 恰一非空: file 类经 extract_text, link 类为 URL 文本行,
    学堂条目经 html_to_text; error 条目仍保留(列名告知).
    """
    entries = []
    for rid in resource_ids or []:
        row = storage.get_resource(rid)
        if row is None:
            entries.append(("资源", "file", f"资源 #{rid}", "",
                            "资源不存在"))
            continue
        source = _course_label(storage, row["course_id"])
        entries.append(_resource_entry(source, row, max_pdf_pages))
    for cid in course_ids or []:
        course = storage.get_course(cid)
        if course is None:
            entries.append(("课程", "file", f"课程 #{cid}", "", "课程不存在"))
            continue
        source = course["name"]
        for row in storage.list_resources(cid):
            entries.append(_resource_entry(source, row, max_pdf_pages))
        if include_learn:
            entries.extend(_learn_entries(storage, cid))
    return entries


def _resource_entry(source: str, row, max_pdf_pages: int) -> tuple:
    """单条资源行 -> 条目; kind=dir 跳过, link 为 URL 文本行."""
    label = row["display_name"] or os.path.basename(
        row["path_or_url"].rstrip("/")) or row["path_or_url"]
    kind = row["kind"]
    if kind == "dir":
        return (source, "dir", label, "", "目录索引不纳入")
    if kind == "link":
        return (source, "link", label, f"链接: {row['path_or_url']}", "")
    text, error = extract_text(row["path_or_url"], _EXTRACT_LIMIT,
                               max_pdf_pages)
    return (source, "file", label, text, error)


def _learn_entries(storage, course_id: int) -> list:
    """某课程关联的学堂公告与作业描述条目."""
    entries = []
    linked = [row for row in storage.learn_list_courses()
              if row["course_id"] == course_id]
    for course in linked:
        source = f"学堂缓存: {course['kcm']}"
        for notice in storage.learn_list_notices(course["wlkcid"]):
            body = notice["ggnr"] or ""
            if not body:
                continue
            entries.append((source, "learn_notice",
                            f"公告: {notice['bt'] or '(无标题)'}",
                            html_to_text(body), ""))
        for homework in storage.learn_list_homework(course["wlkcid"]):
            body = homework["detail_html"] or ""
            if not body:
                continue
            entries.append((source, "learn_homework",
                            f"作业: {homework['bt'] or '(无标题)'}",
                            html_to_text(body), ""))
    return entries


def _course_label(storage, course_id: int) -> str:
    """课程名; 课程已删返回占位串."""
    course = storage.get_course(course_id)
    return course["name"] if course is not None else f"课程 #{course_id}"


def _extract_plain(path: str, limit_chars: int) -> tuple:
    """纯文本直读(utf-8 容错解码)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(limit_chars + 1)
    except OSError:
        return "", "无法读取文件"
    return text[:limit_chars], ""


def _extract_pdf(path: str, limit_chars: int, max_pdf_pages: int) -> tuple:
    """PDF 按页提取(pypdf 函数内导入, 未装时降级为明确错误)."""
    try:
        # 函数内导入: 未安装 pypdf 只影响 PDF 路径, 不影响模块导入
        from pypdf import PdfReader  # pylint: disable=import-outside-toplevel
    except ImportError:
        return "", "未安装 pypdf (pip install pypdf)"
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            return "", "加密 PDF"
        pages = reader.pages[:max_pdf_pages]
        texts = []
        for page in pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:               # pylint: disable=broad-exception-caught
                texts.append("")
        full = "\n".join(texts).strip()
    except Exception as exc:                # pylint: disable=broad-exception-caught
        return "", f"PDF 无法解析: {exc}"
    if not full:
        return "", "无文本层(可能是扫描件)"
    return full[:limit_chars], ""


def _extract_docx(path: str, limit_chars: int) -> tuple:
    """docx 粗提: word/document.xml 的 w:p 段落与 w:t 文本."""
    try:
        with zipfile.ZipFile(path) as bundle:
            root = ElementTree.fromstring(bundle.read("word/document.xml"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return "", "docx 无法解析"
    paragraphs = []
    for paragraph in root.iter(_WORD_NS + "p"):
        paragraphs.append("".join(
            node.text or "" for node in paragraph.iter(_WORD_NS + "t")))
    return "\n".join(paragraphs)[:limit_chars], ""


def _extract_pptx(path: str, limit_chars: int) -> tuple:
    """pptx 粗提: ppt/slides/slideN.xml 按序号排序的 a:t 文本."""
    try:
        with zipfile.ZipFile(path) as bundle:
            names = [name for name in bundle.namelist()
                     if _SLIDE_RE.match(name)]
            names.sort(key=lambda name: int(_SLIDE_RE.match(name).group(1)))
            slides = []
            for name in names:
                root = ElementTree.fromstring(bundle.read(name))
                slides.append("".join(
                    node.text or "" for node in root.iter(
                        _DRAWING_NS + "t")))
    except (OSError, ValueError, zipfile.BadZipFile):
        return "", "pptx 无法解析"
    return "\n".join(slides)[:limit_chars], ""
