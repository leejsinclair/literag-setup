"""T034 — FR-013, Story 1 scenario 4.

The same `md5Checksum` in two subfolders gives one staged file and one ingest, with both
folder locations recorded.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_identical_pdf_in_two_folders_is_stored_and_ingested_once(h):
    assert h.drive.files["pdf1"]["md5Checksum"] == h.drive.files["pdf1copy"]["md5Checksum"]
    h.pull()
    assert h.store_file("drive", "pdf1", ext="pdf").is_file()
    assert not list(h.inputs_dir.rglob("pdf1copy*"))

    rec = h.records()
    canon, dup = rec.get_item("drive", "pdf1"), rec.get_item("drive", "pdf1copy")
    assert dup.disposition == "duplicate"
    assert dup.duplicate_of == canon.id
    assert canon.disposition == "ingest"
    # Both folder locations are recorded against the content.
    assert set(rec.locations(canon.id)) | set(rec.locations(dup.id)) == {"hiring", "deep"}
    entry = next(e for e in h.last_log("pull")["items"] if e["item_id"] == dup.id)
    assert entry["action"] == "duplicate"
    assert "Onboarding checklist.pdf" in entry["reason"]
    assert "hiring" in entry["reason"] and "deep" in entry["reason"]

    h.plan()
    refs = [s["ref"] for s in h.plan_json()["stages"]]
    assert refs.count("drive:pdf1") == 1
    assert "drive:pdf1copy" not in refs

    h.ingest()
    pdf_docs = [d for d in h.lightrag.docs.values() if d["file_path"].startswith("drive__pdf1")]
    assert [d["file_path"] for d in pdf_docs] == ["drive__pdf1.pdf"]


def test_duplicate_arriving_later_is_not_ingested_again(h):
    h.pull()
    h.ingest()
    h.drive.add_file("pdf1third", "Onboarding (third copy).pdf", "application/pdf", ["lead"],
                     content=h.drive.content["pdf1"])
    h.pull()
    rec = h.records()
    third = rec.get_item("drive", "pdf1third")
    assert third.disposition == "duplicate"
    assert third.duplicate_of == rec.get_item("drive", "pdf1").id
    h.plan()
    assert h.plan_json()["stages"] == []


def test_canonical_trashed_promotes_the_duplicate(h):
    h.pull()
    h.ingest()
    h.drive.trash("pdf1")
    h.pull()
    rec = h.records()
    heir = rec.get_item("drive", "pdf1copy")
    assert heir.disposition == "ingest" and heir.duplicate_of is None
    assert (heir.status, heir.pending) == ("stale", "ingest")
    assert h.store_file("drive", "pdf1copy", ext="pdf").read_bytes() == h.drive.content["pdf1copy"]
    _, applied = h.ingest()
    assert applied.rc == 0, applied
    assert applied.fields()["added"] == "1" and applied.fields()["removed"] == "1"
    assert h.lightrag.doc_for("drive__pdf1.pdf") is None
    assert h.lightrag.doc_for("drive__pdf1copy.pdf") is not None
