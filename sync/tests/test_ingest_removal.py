"""T038 — FR-010 (removal), FR-011, Story 1 scenario 3, SC-008.

A trashed item is removed only when `--backup` names an archive newer than the plan, and the
run log records title, source, reason and `backup_ref`. Eleven removals with the limit at 10
hold all of them, still ingest the additions, and exit 4. `--confirm-removals` then
performs them.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness, pull_counts

OLD_FACT = "called **Bluebird**"  # doc1 only; the Headcount sheet also names Bluebird


@pytest.fixture
def h(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    h.pull()
    h.ingest()
    return h


def test_trashed_item_is_removed_after_a_newer_backup_and_logged(h):
    old_id = h.records().get_item("drive", "doc1").doc_id
    h.drive.trash("doc1")
    r = h.pull()
    assert pull_counts(r)["removed"] == 1
    item = h.records().get_item("drive", "doc1")
    assert (item.status, item.pending, item.removal_reason) == ("active", "remove", "trashed_upstream")

    f = h.plan().fields()
    assert (f["removals"], f["held"]) == ("1", "0")

    # Without a backup, or with one older than the plan: refused, nothing removed.
    assert h.apply(f["plan_id"]).rc == 4
    assert h.apply(f["plan_id"], h.backup_after_plan(seconds=-1)).rc == 4
    assert h.apply(f["plan_id"], "not-a-backup.tar.gz").rc == 4
    assert old_id in h.lightrag.docs

    backup = h.backup_after_plan()
    applied = h.apply(f["plan_id"], backup)
    assert applied.rc == 0, applied
    assert applied.last == f"added=0 replaced=0 removed=1 held=0 skipped=1 failed=0 backup={backup}"
    assert old_id not in h.lightrag.docs
    assert not h.lightrag.knows(OLD_FACT)

    item = h.records().get_item("drive", "doc1")
    assert (item.status, item.pending, item.doc_id) == ("removed", "none", None)
    # The store file is moved, never deleted.
    assert not h.store_file("drive", "doc1").exists()
    assert (h.inputs_dir / "drive" / ".removed" / "doc1.md").is_file()

    log = h.last_log("ingest")
    assert log["backup_ref"] == backup
    assert log["counts"]["removed"] == 1
    (entry,) = log["removals"]
    assert entry["title"] == "Team topologies notes"
    assert entry["source"] == "drive"
    assert entry["reason"] == "trashed_upstream"
    assert entry["backup_ref"] == backup
    assert entry["outcome"] == "removed"


def test_permanently_deleted_item_has_reason_deleted_upstream(h):
    h.drive.delete("doc2")
    h.pull()
    item = h.records().get_item("drive", "doc2")
    assert (item.pending, item.removal_reason) == ("remove", "deleted_upstream")
    _, applied = h.ingest()
    assert applied.rc == 0
    assert h.last_log("ingest")["removals"][0]["reason"] == "deleted_upstream"


def _add_and_ingest_eleven(h):
    for n in range(11):
        h.drive.add_doc(f"bulk{n}", f"Bulk note {n}", ["hiring"], f"# Bulk {n}\n\nbulk fact {n}\n")
    h.pull()
    h.ingest()
    assert all(h.lightrag.doc_for(f"drive__bulk{n}.md") for n in range(11))


def test_eleven_removals_over_limit_ten_are_all_held_but_additions_land(h):
    _add_and_ingest_eleven(h)
    for n in range(11):
        h.drive.trash(f"bulk{n}")
    h.drive.add_doc("fresh", "Fresh doc", ["lead"], "# Fresh\n\nfresh fact\n")
    h.pull()

    planned = h.plan()
    f = planned.fields()
    assert (f["removals"], f["held"]) == ("0", "11")
    plan = h.plan_json()
    assert plan["removals"] == []
    assert len(plan["held"]) == 11
    assert {e["reason"] for e in plan["held"]} == {"trashed_upstream"}

    applied = h.apply(f["plan_id"])  # no removals planned, so no backup is needed
    assert applied.rc == 4
    s = applied.fields()
    assert (s["added"], s["removed"], s["held"], s["backup"]) == ("1", "0", "11", "none")
    assert h.lightrag.doc_for("drive__fresh.md") is not None
    assert all(h.lightrag.doc_for(f"drive__bulk{n}.md") for n in range(11))
    for n in range(11):
        item = h.records().get_item("drive", f"bulk{n}")
        assert item.held is True and item.pending == "remove"
    log = h.last_log("ingest")
    held = [e for e in log["removals"] if e["outcome"] == "held"]
    assert len(held) == 11
    assert all(e["title"].startswith("Bulk note") and e["source"] == "drive"
               and e["reason"] == "trashed_upstream" for e in held)

    # The owner confirms: all eleven are removed, after a newer backup.
    planned, applied = h.ingest(confirm=True)
    assert planned.fields()["removals"] == "11" and planned.fields()["held"] == "0"
    assert applied.rc == 0, applied
    assert applied.fields()["removed"] == "11"
    assert not any(h.lightrag.doc_for(f"drive__bulk{n}.md") for n in range(11))
    removed = [e for e in h.last_log("ingest")["removals"] if e["outcome"] == "removed"]
    assert len(removed) == 11 and all(e["backup_ref"] for e in removed)


def test_replacements_count_toward_the_limit(h):
    h.set_env(KB_SYNC_REMOVAL_LIMIT="1")
    h.drive.edit_doc("doc1", "# Team topologies notes\n\nOsprey now.\n")
    h.drive.trash("doc2")
    h.drive.add_doc("fresh", "Fresh doc", ["lead"], "# Fresh\n\nfresh fact\n")
    h.pull()
    f = h.plan().fields()
    assert (f["removals"], f["held"]) == ("0", "2")
    plan = h.plan_json()
    assert [s["ref"] for s in plan["stages"]] == ["drive:fresh"]
    applied = h.apply(f["plan_id"])
    assert applied.rc == 4
    # The held replacement stays stale and its old text stays in the knowledge base.
    item = h.records().get_item("drive", "doc1")
    assert (item.status, item.pending) == ("stale", "replace")
    assert h.lightrag.knows(OLD_FACT)
    assert h.lightrag.doc_for("drive__fresh.md") is not None


def test_reappearing_item_clears_its_pending_removal(h):
    h.drive.trash("doc1")
    h.pull()
    h.drive.files["doc1"]["trashed"] = False
    h.drive._change("doc1")
    h.pull()
    item = h.records().get_item("drive", "doc1")
    assert (item.status, item.pending, item.held) == ("active", "none", False)
    assert h.plan().fields()["removals"] == "0"
