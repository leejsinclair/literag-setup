"""T037 — FR-010 (replace) and FR-008, Story 1 scenario 2.

For an edited item, `ingest --apply` deletes the old `doc_id` and waits for idle before
scanning the new staged copy, then binds the new `doc_id`.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness

OLD_FACT = "called **Bluebird**"  # doc1 only; the Headcount sheet also names Bluebird

NEW_TEXT = "# Team topologies notes\n\nThe platform team is now called Osprey.\n"


@pytest.fixture
def h(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.pull()
    h.ingest()
    return h


def test_first_ingest_binds_doc_ids(h):
    item = h.records().get_item("drive", "doc1")
    assert item.status == "active" and item.pending == "none"
    doc = h.lightrag.doc_for("drive__doc1.md")
    assert item.doc_id == doc["id"]
    assert item.staged_name == "drive__doc1.md"
    assert item.ingested_fingerprint == item.fingerprint


def test_edit_deletes_old_doc_waits_idle_then_scans_and_binds_new(h):
    old_id = h.records().get_item("drive", "doc1").doc_id
    assert h.lightrag.knows(OLD_FACT)
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()

    planned = h.plan()
    f = planned.fields()
    assert f["removals"] == "1"  # the old document's delete counts as a removal
    plan = h.plan_json()
    assert plan["stages"][0]["ref"] == "drive:doc1"
    assert plan["stages"][0]["action"] == "replace"
    assert plan["stages"][0]["old_doc_id"] == old_id
    assert plan["removals"][0]["reason"] == "replaced"

    h.lightrag.sequence.clear()
    backup = h.backup_after_plan()
    applied = h.apply(f["plan_id"], backup)
    assert applied.rc == 0, applied
    assert applied.last == f"added=0 replaced=1 removed=0 held=0 skipped=1 failed=0 backup={backup}"

    seq = h.lightrag.sequence
    i_del = seq.index(("DELETE", "/documents/delete_document"))
    i_idle = seq.index(("GET", "/documents/pipeline_status"))
    i_scan = seq.index(("POST", "/documents/scan"))
    assert i_del < i_idle < i_scan

    item = h.records().get_item("drive", "doc1")
    assert item.status == "active" and item.pending == "none"
    assert item.doc_id != old_id
    assert old_id not in h.lightrag.docs
    assert h.lightrag.docs[item.doc_id]["file_path"] == "drive__doc1.md"
    assert h.lightrag.knows("Osprey")
    assert not h.lightrag.knows(OLD_FACT)
    assert len(h.lightrag.docs_named("drive__doc1.md")) == 1

    entry = next(e for e in h.last_log("ingest")["removals"] if e["doc_id"] == old_id)
    assert entry["reason"] == "replaced"
    assert entry["backup_ref"] == backup
    assert entry["outcome"] == "removed"


def test_replace_without_backup_is_refused(h):
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()
    f = h.plan().fields()
    applied = h.apply(f["plan_id"])
    assert applied.rc == 4
    assert "--backup" in applied.err
    assert h.lightrag.knows(OLD_FACT)


def test_backup_older_than_plan_is_refused(h):
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()
    f = h.plan().fields()
    applied = h.apply(f["plan_id"], h.backup_after_plan(seconds=-60))
    assert applied.rc == 4
    assert h.lightrag.knows(OLD_FACT)


def test_plan_id_mismatch_is_refused(h):
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()
    h.plan()
    applied = h.apply("20200101T000000Z-dead", h.backup_after_plan())
    assert applied.rc == 4
    assert h.lightrag.knows(OLD_FACT)


def test_a_plan_cannot_be_applied_twice(h):
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()
    f = h.plan().fields()
    backup = h.backup_after_plan()
    assert h.apply(f["plan_id"], backup).rc == 0
    assert h.apply(f["plan_id"], backup).rc == 4


def test_server_stopped_exits_3(h):
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()
    f = h.plan().fields()
    h.lightrag.down = True
    applied = h.apply(f["plan_id"], h.backup_after_plan())
    assert applied.rc == 3
    assert "LightRAG server is not running at http://127.0.0.1:9621" in applied.err
