#!/usr/bin/env python3
"""Generate tests/fixtures/sample.pdf — a minimal, text-searchable PDF.

Standard-library only (no reportlab/fpdf). Produces a single-page PDF whose text
layer contains one distinctive, self-contained fact used by the smoke test's C4
and C5 checks. Re-run this if you need to regenerate the fixture:

    python3 tests/fixtures/make_sample_pdf.py
"""
from pathlib import Path

LINES = [
    "Obsidian Kestrel Ledger - Reference Note",
    "",
    "This fixture PDF exists only to prove the PDF ingestion path end to end",
    "(smoke-test checks C4 and C5). The fact below is invented and appears in no",
    "other document.",
    "",
    "Key fact: The Obsidian Kestrel Ledger records that shipment QK-4417",
    "contained exactly 1,204 brass astrolabes consigned to Port Vandermeer,",
    "dispatched by the factor Sabine Threlkeld on 11 September 1908.",
    "",
    "Secondary detail: the ledger's binding is stamped with the catalogue",
    "mark OKL-1908-QK.",
]


def escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_content_stream() -> bytes:
    parts = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for line in LINES:
        parts.append(f"({escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("latin-1")


def build_pdf() -> bytes:
    content = build_content_stream()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"

    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode("latin-1")
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
    return bytes(out)


if __name__ == "__main__":
    target = Path(__file__).with_name("sample.pdf")
    target.write_bytes(build_pdf())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
