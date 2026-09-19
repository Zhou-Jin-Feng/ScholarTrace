from __future__ import annotations

import re

from scholartrace.delivery.reporting import render_pdf


def test_chinese_multpage_pdf_retains_final_line_and_long_lines() -> None:
    tail = "结束标识：所有中文内容完整保留"
    text = "# 中文研究报告\n" + "\n".join(f"第{i}行：证据追溯与预算记录" for i in range(160))
    text += "\n" + "长段落不能截断" * 200 + "\n" + tail
    pdf = render_pdf(markdown=text)
    assert pdf.startswith(b"%PDF-1.4")
    assert b"/Type /Pages" in pdf
    pages = re.search(rb"/Count (\d+)", pdf)
    assert pages is not None and int(pages[1]) >= 4
    assert tail.encode("utf-16-be").hex().upper().encode() in pdf
    assert b"/ToUnicode" in pdf
    assert render_pdf(markdown=text) == pdf


def test_pdf_escapes_content_and_preserves_ascii_and_empty_document() -> None:
    pdf = render_pdf(markdown="(literal) \\ /JS </script>\n末行")
    assert b"\\(literal\\)" in pdf
    assert b"/JavaScript" not in pdf
    assert "末行".encode("utf-16-be").hex().upper().encode() in pdf
    assert b"/Count 1" in render_pdf(markdown="")
