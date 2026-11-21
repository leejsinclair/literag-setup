"""Spreadsheet to Markdown (T040; FR-012, Story 1 scenario 6; contracts/store-formats.md).

An `.xlsx` workbook (a Drive upload, or a Google Sheet exported as xlsx) becomes one
`## <sheet name>` heading and one Markdown table per sheet. The first non-empty row is the
header. Cells are escaped so a `|` or a line break cannot break the table.
"""

from __future__ import annotations

import io
from datetime import date, datetime, time

import openpyxl


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime, date, time)):
        value = value.isoformat()
    text = str(value).replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>").strip()


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def sheet_table(rows: list[tuple]) -> str:
    """One sheet's rows as a Markdown table, or a note when the sheet is empty."""
    cells = [[_cell(v) for v in row] for row in rows]
    cells = [row for row in cells if any(row)]
    if not cells:
        return "_(empty sheet)_"
    width = max(max((i + 1 for i, c in enumerate(row) if c), default=0) for row in cells)
    cells = [(row + [""] * width)[:width] for row in cells]
    lines = [_row(cells[0]), _row(["---"] * width)]
    lines += [_row(row) for row in cells[1:]]
    return "\n".join(lines)


def xlsx_to_markdown(data: bytes) -> str:
    """Convert an xlsx workbook to Markdown: one heading and table per sheet."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        parts = [f"## {ws.title}\n\n{sheet_table(list(ws.iter_rows(values_only=True)))}"
                 for ws in wb.worksheets]
    finally:
        wb.close()
    return "\n\n".join(parts) + "\n"
