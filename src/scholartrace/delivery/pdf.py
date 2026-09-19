"""Deterministic paginated text PDF with the standard Adobe-GB1 CJK font.

No network, browser, or platform font path is used. Readers must support the
standard STSong-Light substitution font; this is not an embedded-font PDF/A.
Markdown remains literal text, never executable HTML or a remote resource.
"""

from __future__ import annotations

import itertools


def _stream(data: bytes) -> bytes:
    return b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


def _width(char: str, size: int) -> float:
    return size * (0.6 if ord(char) < 128 else 1.0)


def _wrap(text: str, size: int) -> list[str]:
    lines: list[str] = []
    current = ""
    width = 0.0
    for char in text.expandtabs(4):
        advance = _width(char, size)
        if current and width + advance > 491:
            lines.append(current)
            current, width = "", 0.0
        current += char
        width += advance
    lines.append(current)
    return lines


def _text(text: str, *, x: int, y: int, size: int) -> str:
    parts = [f"BT 1 0 0 1 {x} {y} Tm"]
    for ascii_only, chars in itertools.groupby(text, key=lambda c: ord(c) < 128):
        run = "".join(chars)
        if ascii_only:
            escaped = run.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            parts.append(f"/FA {size} Tf ({escaped}) Tj")
        else:
            encoded = run.encode("utf-16-be").hex().upper()
            parts.append(f"/FC {size} Tf <{encoded}> Tj")
    return "\n".join([*parts, "ET"])


def build_pdf(markdown: str) -> bytes:
    pages: list[list[str]] = [[]]
    y = 760
    for line in markdown.splitlines():
        size = 17 if line.startswith("# ") else 13 if line.startswith("## ") else 10
        leading = size + 7
        for wrapped in _wrap(line, size):
            if y < 66:
                pages.append([])
                y = 760
            pages[-1].append(_text(wrapped, x=52, y=y, size=size))
            y -= leading
    # Identity ToUnicode mapping for the UCS-2 code space keeps Chinese copyable.
    cmap = b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /ScholarTrace-UCS def /CMapType 2 def
1 begincodespacerange <0000> <FFFF> endcodespacerange
1 beginbfrange <0000> <FFFF> <0000> endbfrange
endcmap CMapName currentdict /CMap defineresource pop end end"""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # Page tree, filled after child object numbers are known.
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        (b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
         b"/Encoding /UniGB-UCS2-H /DescendantFonts [5 0 R] /ToUnicode 7 0 R >>"),
        (b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
         b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
         b"/FontDescriptor 6 0 R /DW 1000 >>"),
        (b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 "
         b"/FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 "
         b"/Descent -120 /CapHeight 880 /StemV 80 >>"),
        _stream(cmap),
    ]
    kids: list[str] = []
    for index, commands in enumerate(pages, 1):
        number = len(objects) + 1
        kids.append(f"{number} 0 R")
        objects.append(
            (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
             f"/Resources << /Font << /FA 3 0 R /FC 4 0 R >> >> "
             f"/Contents {number + 1} 0 R >>").encode()
        )
        header = _text("ScholarTrace / Research Report", x=52, y=803, size=9)
        footer = _text(f"{index} / {len(pages)}", x=52, y=34, size=9)
        content = "\n".join([
            "0.16 0.31 0.27 rg", header, "0.83 0.88 0.83 RG 52 789 m 543 789 l S",
            "0.12 0.16 0.14 rg", *commands, "0.36 0.42 0.38 rg", footer,
        ])
        objects.append(_stream(content.encode("ascii")))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)
