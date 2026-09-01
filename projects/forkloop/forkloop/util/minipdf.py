"""A tiny dependency-free PDF writer for one-or-more pages of monospaced text.

Used to generate synthetic patient documents (authorization letters) that the
agent must open and read inside OpenEMR. Output is a valid PDF 1.4 file that
Chrome's built-in viewer renders.
"""

from __future__ import annotations

from typing import Iterable, Sequence

PAGE_W, PAGE_H = 612, 792  # US Letter, points


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: Sequence[Sequence[str]], *, font_size: int = 11, leading: int = 14,
              margin: int = 56, title: str = "") -> bytes:
    """Build a PDF from a list of pages, each a list of text lines."""
    if not pages:
        pages = [[""]]
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    pages_id_placeholder = len(objects) + 1 + 2 * len(pages)  # computed after pages/contents
    for lines in pages:
        y = PAGE_H - margin
        parts = [f"BT /F1 {font_size} Tf {leading} TL {margin} {y} Td"]
        for line in lines:
            parts.append(f"({_escape(line)}) Tj T*")
        parts.append("ET")
        stream = "\n".join(parts).encode("latin-1", "replace")
        cid = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_ids.append(cid)
        pid = add(
            (f"<< /Type /Page /Parent {pages_id_placeholder} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
             f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {cid} 0 R >>").encode()
        )
        page_ids.append(pid)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    assert pages_id == pages_id_placeholder
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    info_id = add(f"<< /Title ({_escape(title)}) /Producer (forkloop minipdf) >>".encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def wrap_lines(text: str, width: int = 78) -> list[str]:
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= width:
                cur += " " + w
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return out


def text_pages(paragraphs: Iterable[str], *, lines_per_page: int = 48, width: int = 78) -> list[list[str]]:
    lines: list[str] = []
    for p in paragraphs:
        lines.extend(wrap_lines(p, width))
        lines.append("")
    pages: list[list[str]] = []
    for i in range(0, max(1, len(lines)), lines_per_page):
        pages.append(lines[i:i + lines_per_page])
    return pages or [[""]]


__all__ = ["build_pdf", "wrap_lines", "text_pages"]
