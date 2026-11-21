"""T032 — FR-009, SC-004, edge case "Moved or renamed upstream".

With no upstream change, a second `pull` and `ingest --plan` stage zero items. A move or
rename keeps the item's ID and store path, and changes only its title, link or locations.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness, pull_counts
from kb_sync import store


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_no_change_second_pull_and_plan_stage_nothing(h):
    h.pull()
    h.ingest()
    downloads_before = len(h.drive.downloads())
    lightrag_docs = dict(h.lightrag.docs)

    r = h.pull()
    c = pull_counts(r)
    assert (c["new"], c["changed"], c["removed"], c["failed"]) == (0, 0, 0, 0)
    assert c["unchanged"] > 0
    # Only the changes feed was read: nothing was downloaded or exported again.
    assert len(h.drive.downloads()) == downloads_before

    f = h.plan().fields()
    assert (f["removals"], f["held"]) == ("0", "0")
    assert h.plan_json()["stages"] == []
    _, applied = h.ingest()
    assert applied.fields()["added"] == "0" and applied.fields()["replaced"] == "0"
    assert h.lightrag.docs.keys() == lightrag_docs.keys()


def test_second_pull_uses_the_changes_feed_not_a_walk(h):
    def lists():
        return [r for r in h.drive.requests if r.url.path == "/drive/v3/files"]

    h.pull()
    walked = len(lists())
    assert walked > 0
    h.pull()
    assert len(lists()) == walked  # no files.list on an incremental pull
    assert [r for r in h.drive.requests if r.url.path == "/drive/v3/changes"]


def test_rename_keeps_id_and_path_and_does_not_restage(h):
    h.pull()
    h.ingest()
    rec = h.records()
    before = rec.get_item("drive", "doc1")
    path = h.store_file("drive", "doc1")

    h.drive.rename("doc1", "Team Topologies (renamed)")
    r = h.pull()
    c = pull_counts(r)
    assert (c["new"], c["changed"]) == (0, 0)
    after = h.records().get_item("drive", "doc1")
    assert after.id == before.id
    assert after.title == "Team Topologies (renamed)"
    assert after.store_path == before.store_path
    assert after.status == "active" and after.pending == "none"
    meta, _ = store.read_markdown(path)
    assert meta["title"] == "Team Topologies (renamed)"
    h.plan()
    assert h.plan_json()["stages"] == []


def test_move_changes_only_locations(h):
    h.pull()
    h.ingest()
    before = h.records().get_item("drive", "doc1")
    h.drive.move("doc1", ["deep"])
    r = h.pull()
    assert pull_counts(r)["changed"] == 0
    rec = h.records()
    after = rec.get_item("drive", "doc1")
    assert after.id == before.id and after.store_path == before.store_path
    assert rec.locations(after.id) == ["deep"]
    assert after.pending == "none"
    h.plan()
    assert h.plan_json()["stages"] == []


def test_edit_marks_replace_and_only_that_item_is_staged(h):
    h.pull()
    h.ingest()
    h.drive.edit_doc("doc1", "# Team topologies notes\n\nThe platform team is now called Osprey.\n")
    r = h.pull()
    assert pull_counts(r)["changed"] == 1
    item = h.records().get_item("drive", "doc1")
    assert (item.status, item.pending) == ("stale", "replace")
    h.plan()
    stages = h.plan_json()["stages"]
    assert [(s["ref"], s["action"]) for s in stages] == [("drive:doc1", "replace")]
