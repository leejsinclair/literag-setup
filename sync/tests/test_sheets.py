"""T040 — `.xlsx` to Markdown tables: one per sheet, header row, cells escaped."""

from __future__ import annotations

from datetime import datetime

from drive_fakes import workbook_bytes
from kb_sync.sources import sheets


def test_one_heading_and_table_per_sheet():
    md = sheets.xlsx_to_markdown(workbook_bytes({
        "People": [["Name", "Role"], ["Ana", "Lead"]],
        "Teams": [["Team"], ["Bluebird"]],
    }))
    assert md.index("## People") < md.index("## Teams")
    assert "| Name | Role |\n| --- | --- |\n| Ana | Lead |" in md
    assert "| Team |\n| --- |\n| Bluebird |" in md


def test_cells_are_escaped_and_normalised():
    md = sheets.xlsx_to_markdown(workbook_bytes({
        "S": [["A", "B", "C"], ["x | y", "line1\nline2", 3.0], [None, datetime(2026, 1, 2, 3, 4), 2.5]],
    }))
    assert "| x \\| y | line1<br>line2 | 3 |" in md
    assert "|  | 2026-01-02T03:04:00 | 2.5 |" in md


def test_ragged_rows_are_padded_and_empty_sheet_is_noted():
    md = sheets.xlsx_to_markdown(workbook_bytes({
        "Ragged": [["A"], ["1", "2", "3"]],
        "Empty": [],
    }))
    assert "| A |  |  |\n| --- | --- | --- |\n| 1 | 2 | 3 |" in md
    assert "## Empty\n\n_(empty sheet)_" in md
