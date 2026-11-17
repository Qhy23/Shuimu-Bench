"""ai_context.py 单元测试: HTML 转文本, 各格式提取, 预算截断与学堂缓存."""

import zipfile

from pypdf import PdfWriter

from shuimu.core.ai_context import (
    MAX_READ_BYTES,
    build_context,
    extract_text,
    html_to_text,
)
from shuimu.core.learn import LearnCourse, LearnHomework, LearnNotice
from shuimu.core.storage import Storage

WORD_XMLNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_XMLNS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PPT_XMLNS = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _minimal_pdf(text: str) -> bytes:
    """手写最小合法 PDF(单页 Helvetica 一行文本, 无压缩)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()
    return bytes(out)


def _make_docx(path, paragraphs):
    """运行时手拼最小 docx(zip + word/document.xml)."""
    body = "".join(
        f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>' for text in paragraphs)
    markup = (f'<?xml version="1.0"?><w:document xmlns:w="{WORD_XMLNS}">'
              f'<w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("word/document.xml", markup)


def _make_pptx(path, slides):
    """运行时手拼最小 pptx(zip + ppt/slides/slideN.xml)."""
    with zipfile.ZipFile(path, "w") as bundle:
        for index, texts in enumerate(slides, 1):
            shapes = "".join(f"<a:t>{text}</a:t>" for text in texts)
            markup = (f'<?xml version="1.0"?><p:sld xmlns:p="{PPT_XMLNS}"'
                      f' xmlns:a="{DRAWING_XMLNS}">'
                      f"<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>")
            bundle.writestr(f"ppt/slides/slide{index}.xml", markup)


class TestHtmlToText:
    """HTML 转纯文本."""

    def test_tags_removed_and_entities_decoded(self):
        """标签剥离, 实体反解."""
        assert html_to_text("<p>a &amp; b &lt;c&gt;</p>") == "a & b <c>"

    def test_li_prefix_and_breaks(self):
        """li 加 "- " 前缀, br/p 结尾换行."""
        markup = "<ul><li>one</li><li>two</li></ul><p>para<br>next</p>"
        assert html_to_text(markup) == "- one\n- two\npara\nnext"

    def test_script_style_removed(self):
        """script/style 块整体去除."""
        markup = "<style>.x{}</style><p>keep</p><script>bad()</script>"
        assert "keep" in html_to_text(markup)
        assert ".x" not in html_to_text(markup)
        assert "bad" not in html_to_text(markup)

    def test_blank_lines_squeezed(self):
        """连续空行压缩为一行, 首尾空白去除."""
        assert html_to_text("<p>a</p><div></div><div></div><p>b</p>") \
            == "a\nb"


class TestExtractText:
    """磁盘文件提取."""

    def test_plain_text(self, tmp_path):
        """txt 直读并按上限截断."""
        path = tmp_path / "note.txt"
        path.write_text("水木工作台", encoding="utf-8")
        text, error = extract_text(str(path), 100)
        assert (text, error) == ("水木工作台", "")
        text, _ = extract_text(str(path), 2)
        assert text == "水木"

    def test_missing_file(self, tmp_path):
        """文件不存在返回错误."""
        _, error = extract_text(str(tmp_path / "none.txt"), 10)
        assert error == "文件不存在"

    def test_unsupported_extension(self, tmp_path):
        """白名单外扩展名明确报不支持."""
        path = tmp_path / "image.png"
        path.write_bytes(b"\x89PNG")
        _, error = extract_text(str(path), 10)
        assert "不支持的格式" in error

    def test_no_extension(self, tmp_path):
        """无扩展名文件报不支持."""
        path = tmp_path / "Makefile"
        path.write_text("all:", encoding="utf-8")
        _, error = extract_text(str(path), 10)
        assert "不支持的格式" in error

    def test_oversize_skipped(self, tmp_path):
        """超过 MAX_READ_BYTES 硬顶拒绝读取."""
        path = tmp_path / "big.txt"
        path.write_bytes(b"x" * (MAX_READ_BYTES + 1))
        _, error = extract_text(str(path), 10)
        assert "文件过大" in error

    def test_html_file(self, tmp_path):
        """html 文件经 html_to_text."""
        path = tmp_path / "page.html"
        path.write_text("<p>hello <b>world</b></p>", encoding="utf-8")
        text, error = extract_text(str(path), 100)
        assert (text, error) == ("hello world", "")

    def test_pdf_with_text(self, tmp_path):
        """手写最小 PDF 可提取文本."""
        path = tmp_path / "doc.pdf"
        path.write_bytes(_minimal_pdf("Hello PDF context"))
        text, error = extract_text(str(path), 100)
        assert error == ""
        assert "Hello PDF context" in text

    def test_pdf_encrypted(self, tmp_path):
        """加密 PDF 明确报错."""
        path = tmp_path / "enc.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("secret")
        with open(path, "wb") as f:
            writer.write(f)
        _, error = extract_text(str(path), 100)
        assert error == "加密 PDF"

    def test_pdf_no_text_layer(self, tmp_path):
        """空白页 PDF 报无文本层."""
        path = tmp_path / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with open(path, "wb") as f:
            writer.write(f)
        _, error = extract_text(str(path), 100)
        assert error == "无文本层(可能是扫描件)"

    def test_docx(self, tmp_path):
        """docx 段落提取."""
        path = tmp_path / "讲义.docx"
        _make_docx(str(path), ["第一段", "第二段"])
        text, error = extract_text(str(path), 100)
        assert (text, error) == ("第一段\n第二段", "")

    def test_docx_corrupt(self, tmp_path):
        """坏 zip 报无法解析."""
        path = tmp_path / "bad.docx"
        path.write_bytes(b"not a zip")
        _, error = extract_text(str(path), 100)
        assert error == "docx 无法解析"

    def test_pptx_slide_order(self, tmp_path):
        """pptx 按页序号(非字典序)提取."""
        path = tmp_path / "slides.pptx"
        _make_pptx(str(path), [["s1"], ["s2"], ["s10"]])
        text, error = extract_text(str(path), 100)
        assert (text, error) == ("s1\ns2\ns10", "")


def _storage_with_course(tmp_path, name="高等数学"):
    """建库 + 一门课."""
    storage = Storage(str(tmp_path / "t.db"))
    return storage, storage.add_course(name)


class TestBuildContext:
    """上下文拼装."""

    def test_course_files_and_links(self, tmp_path):
        """课程资源: 文件提取, link 为 URL 行, dir 跳过."""
        storage, cid = _storage_with_course(tmp_path)
        note = tmp_path / "note.txt"
        note.write_text("线性代数笔记", encoding="utf-8")
        storage.add_resource(cid, "file", str(note))
        storage.add_resource(cid, "link", "https://example.com/course",
                             display_name="课程主页")
        storage.add_resource(cid, "dir", str(tmp_path / "subdir"))
        result = build_context(storage, course_ids=[cid])
        assert result.total_chars > 0
        by_label = {item.label: item for item in result.items}
        assert by_label["note.txt"].chars == len("线性代数笔记")
        assert by_label["课程主页"].kind == "link"
        assert "https://example.com/course" in result.text
        assert by_label["subdir"].skipped == "目录索引不纳入"
        assert "=== 资料: note.txt" in result.text

    def test_missing_file_listed_not_fatal(self, tmp_path):
        """失效路径(文件已删)列名告知, 不中断."""
        storage, cid = _storage_with_course(tmp_path)
        storage.add_resource(cid, "file", str(tmp_path / "gone.txt"))
        result = build_context(storage, course_ids=[cid])
        assert result.items[0].skipped == "文件不存在"
        assert "跳过: 文件不存在" in result.text

    def test_per_file_truncation(self, tmp_path):
        """单文件上限截断标 truncated."""
        storage, cid = _storage_with_course(tmp_path)
        note = tmp_path / "long.txt"
        note.write_text("字" * 100, encoding="utf-8")
        storage.add_resource(cid, "file", str(note))
        result = build_context(storage, course_ids=[cid], per_file_limit=10)
        assert result.items[0].chars == 10
        assert result.items[0].truncated

    def test_budget_exhaustion_lists_rest(self, tmp_path):
        """总预算满后余条目 skipped 并列名."""
        storage, cid = _storage_with_course(tmp_path)
        for index in range(3):
            note = tmp_path / f"n{index}.txt"
            note.write_text(f"内容{index}" * 10, encoding="utf-8")
            storage.add_resource(cid, "file", str(note))
        result = build_context(storage, course_ids=[cid], budget=40)
        skipped = [item for item in result.items if item.skipped]
        full = [item for item in result.items if not item.skipped]
        assert result.total_chars <= 40
        assert full and skipped
        assert all(item.skipped == "总预算已满" for item in skipped)

    def test_explicit_resource_ids_first(self, tmp_path):
        """resource_ids 条目排在课程展开之前."""
        storage, cid = _storage_with_course(tmp_path)
        first = tmp_path / "explicit.txt"
        first.write_text("显式勾选", encoding="utf-8")
        rid = storage.add_resource(cid, "file", str(first))
        second = tmp_path / "course.txt"
        second.write_text("课程展开", encoding="utf-8")
        storage.add_resource(cid, "file", str(second))
        result = build_context(storage, course_ids=[cid], resource_ids=[rid])
        assert result.items[0].label == "explicit.txt"

    def test_unknown_ids_reported(self, tmp_path):
        """不存在的课程/资源 id 列名告知."""
        storage, _cid = _storage_with_course(tmp_path)
        result = build_context(storage, course_ids=[999],
                               resource_ids=[999])
        skipped = {item.label: item.skipped for item in result.items}
        assert skipped["资源 #999"] == "资源不存在"
        assert skipped["课程 #999"] == "课程不存在"

    def test_learn_cache_entries(self, tmp_path):
        """学堂缓存: 关联课程的公告与作业描述纳入并转文本."""
        storage, cid = _storage_with_course(tmp_path)
        storage.learn_sync_courses("2026-2027-1", [
            LearnCourse(wlkcid="W1", kcm="AI 编程")])
        storage.learn_set_course_link("W1", cid)
        storage.learn_sync_notices([LearnNotice(
            wlkcid="W1", ggid="G1", bt="期中通知",
            body_html="<p>下周考试</p>")])
        storage.learn_sync_homework([LearnHomework(
            wlkcid="W1", zyid="Z1", state="pending", bt="第一次作业")])
        storage.learn_save_homework_detail("W1", "Z1",
                                           "<p>实现一个客户端</p>")
        result = build_context(storage, course_ids=[cid], include_learn=True)
        labels = [item.label for item in result.items]
        assert "公告: 期中通知" in labels
        assert "作业: 第一次作业" in labels
        assert "下周考试" in result.text
        assert "实现一个客户端" in result.text
        no_learn = build_context(storage, course_ids=[cid])
        assert all(not item.kind.startswith("learn_")
                   for item in no_learn.items)

    def test_learn_cache_ignores_unlinked(self, tmp_path):
        """未关联本地课程的学堂课不纳入."""
        storage, cid = _storage_with_course(tmp_path)
        storage.learn_sync_courses("2026-2027-1", [
            LearnCourse(wlkcid="W9", kcm="未关联课")])
        storage.learn_sync_notices([LearnNotice(
            wlkcid="W9", ggid="G9", bt="孤公告", body_html="<p>x</p>")])
        result = build_context(storage, course_ids=[cid], include_learn=True)
        assert not any("孤公告" in item.label for item in result.items)

    def test_empty_selection(self, tmp_path):
        """无勾选返回空上下文."""
        storage, _cid = _storage_with_course(tmp_path)
        result = build_context(storage)
        assert result.text == ""
        assert not result.items
        assert result.total_chars == 0
