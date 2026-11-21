"""T039 — edge case "Sync interrupted".

Killing `ingest --apply` after the delete but before the scan leaves the item `stale`, and
the next run completes it with no duplicate document.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness, Killed

OLD_FACT = "called **Bluebird**"  # doc1 only; the Headcount sheet also names Bluebird

NEW_TEXT = "# Team topologies notes\n\nThe platform team is now called Osprey.\n"


@pytest.fixture
def h(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.pull()
    h.ingest()
    h.drive.edit_doc("doc1", NEW_TEXT)
    h.pull()
    return h


def _kill():
    raise Killed()


def test_killed_after_delete_before_scan_resumes_without_duplicates(h):
    old_id = h.records().get_item("drive", "doc1").doc_id
    f = h.plan().fields()
    h.lightrag.before["/documents/scan"] = _kill
    with pytest.raises(Killed):
        h.apply(f["plan_id"], h.backup_after_plan())

    assert old_id not in h.lightrag.docs  # the delete happened
    item = h.records().get_item("drive", "doc1")
    assert item.status == "stale"
    assert item.doc_id is None

    del h.lightrag.before["/documents/scan"]
    planned, applied = h.ingest()
    # The old document is gone already, so nothing is deleted twice and no backup is needed.
    assert planned.fields()["removals"] == "0"
    assert applied.rc == 0, applied
    item = h.records().get_item("drive", "doc1")
    assert (item.status, item.pending) == ("active", "none")
    docs = h.lightrag.docs_named("drive__doc1.md")
    assert len(docs) == 1 and docs[0]["id"] == item.doc_id
    assert h.lightrag.knows("Osprey") and not h.lightrag.knows(OLD_FACT)


def test_killed_after_scan_before_bind_binds_existing_doc(h):
    f = h.plan().fields()

    def kill_on_lookup_after_scan():
        if ("POST", "/documents/scan") in h.lightrag.sequence:
            raise Killed()

    # Let the scan and track polling run, then die at the first doc lookup after the scan.
    h.lightrag.sequence.clear()
    h.lightrag.before["/documents/paginated"] = kill_on_lookup_after_scan
    with pytest.raises(Killed):
        h.apply(f["plan_id"], h.backup_after_plan())
    assert len(h.lightrag.docs_named("drive__doc1.md")) == 1
    assert h.records().get_item("drive", "doc1").status == "stale"

    del h.lightrag.before["/documents/paginated"]
    scans_before = h.lightrag.sequence.count(("POST", "/documents/scan"))
    _, applied = h.ingest()
    assert applied.rc == 0, applied
    item = h.records().get_item("drive", "doc1")
    assert item.status == "active"
    docs = h.lightrag.docs_named("drive__doc1.md")
    assert len(docs) == 1 and docs[0]["id"] == item.doc_id
    # Bound from the existing document; no second scan was needed.
    assert h.lightrag.sequence.count(("POST", "/documents/scan")) == scans_before
