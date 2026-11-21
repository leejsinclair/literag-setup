"""T033 — FR-012, Story 1 scenarios 5 and 6, edge case "Oversized Google Doc".

A Doc becomes Markdown and a PDF is downloaded. An `.xlsx` or Sheet becomes one Markdown
table per sheet. Audio, video and images are listed as skipped. A Doc over the 10 MB export
limit is reported as failed with its title and size. A scanned PDF fails in LightRAG at
ingest and is reported, never silently dropped.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness, pull_counts
from kb_sync import store


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


@pytest.fixture
def pulled(h):
    return h, h.pull()


def test_google_doc_is_exported_as_markdown(pulled):
    h, _ = pulled
    _, body = store.read_markdown(h.store_file("drive", "doc1"))
    assert "**Bluebird**" in body  # the text/markdown export, not the plain-text one
    reqs = [r for r in h.drive.requests if r.url.path == "/drive/v3/files/doc1/export"]
    assert reqs[0].url.params["mimeType"] == "text/markdown"


def test_doc_falls_back_to_plain_text_when_markdown_export_is_refused(pulled):
    h, _ = pulled
    _, body = store.read_markdown(h.store_file("drive", "doc2"))
    assert "four signals" in body
    mimes = [r.url.params["mimeType"] for r in h.drive.requests
             if r.url.path == "/drive/v3/files/doc2/export"]
    assert mimes == ["text/markdown", "text/plain"]


def test_pdf_is_downloaded_whole(pulled):
    h, _ = pulled
    pdf = h.store_file("drive", "pdf1", ext="pdf")
    assert pdf.read_bytes() == h.drive.content["pdf1"]
    item = h.records().get_item("drive", "pdf1")
    assert item.staged_name == "drive__pdf1.pdf"
    assert item.url == "https://drive.google.com/file/d/pdf1/view"


def test_google_sheet_becomes_one_table_per_sheet(pulled):
    h, _ = pulled
    meta, body = store.read_markdown(h.store_file("drive", "sheet1"))
    assert meta["title"] == "Headcount plan"
    assert "## Engineering" in body and "## Design" in body
    assert "| Team | Headcount | Lead |" in body
    assert "| Bluebird | 6 | Ana |" in body
    assert "Raj \\| interim" in body  # a pipe inside a cell is escaped
    assert "| --- | --- | --- |" in body


def test_xlsx_becomes_markdown_table(pulled):
    h, _ = pulled
    _, body = store.read_markdown(h.store_file("drive", "xlsx1"))
    assert "## Q1" in body
    assert "| Hiring | 120000 |" in body
    assert "| Training | 15000.5 |" in body


def test_media_is_listed_as_skipped_and_never_downloaded(pulled):
    h, r = pulled
    c = pull_counts(r)
    assert c["skipped"] >= 3
    for fid, title in (("audio1", "Standup recording.m4a"), ("video1", "All hands.mp4"),
                       ("image1", "Org chart.png")):
        assert fid not in h.drive.downloads()
        assert not list(h.inputs_dir.rglob(f"{fid}.*"))
        assert f"skipped: {title} (drive)" in r.err
        assert h.records().get_item("drive", fid).disposition == "skip_media"
    h.plan()
    staged_refs = {s["ref"] for s in h.plan_json()["stages"]}
    assert not staged_refs & {"drive:audio1", "drive:video1", "drive:image1"}


def test_oversized_doc_is_failed_with_title_and_size(pulled):
    h, r = pulled
    assert r.rc == 1
    assert pull_counts(r)["failed"] == 1
    title = "A Primer on Cultivating High-Performing Engineering Teams"
    line = next(line for line in r.err.splitlines() if title in line)
    assert line.startswith(f"failed: {title} (drive)")
    assert "9.9 MB" in line
    assert not h.store_file("drive", "bigdoc").exists()
    item = h.records().get_item("drive", "bigdoc")
    assert item.failure["stage"] == "export"
    log = h.last_log("pull")
    entry = next(e for e in log["items"] if e["title"] == title)
    assert entry["action"] == "failed" and "9.9 MB" in entry["reason"]


def test_oversized_doc_is_not_retried_until_it_changes(pulled):
    h, _ = pulled
    exports = len([r for r in h.drive.requests if r.url.path == "/drive/v3/files/bigdoc/export"])
    r = h.pull()
    assert pull_counts(r)["failed"] == 0
    assert len([r for r in h.drive.requests if r.url.path == "/drive/v3/files/bigdoc/export"]) == exports


def test_scanned_pdf_fails_at_ingest_and_is_reported(pulled):
    h, _ = pulled
    _, applied = h.ingest()
    assert applied.rc == 1
    f = applied.fields()
    assert int(f["failed"]) == 1
    assert "Signed offer letter scan.pdf" in applied.err
    item = h.records().get_item("drive", "scan1")
    assert item.status == "stale"
    assert item.failure["stage"] == "ingest"
    assert "no extractable text" in item.failure["reason"]
    # Everything else that is supported landed.
    for fid in ("doc1", "doc2", "pdf1", "sheet1", "xlsx1", "txt1"):
        assert h.records().get_item("drive", fid).status == "active", fid
    log = h.last_log("ingest")
    assert log["counts"]["failed"] == 1
    assert any(e["title"] == "Signed offer letter scan.pdf" and e["action"] == "failed"
               for e in log["items"])
    # A known failure is not retried on the next run.
    _, again = h.ingest()
    assert again.rc == 0
    assert again.fields()["failed"] == "0"
