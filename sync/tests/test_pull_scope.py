"""T031 — FR-006, Story 1 scenario 1, edge case "Folder removed from config".

`pull` copies files in allow-listed folders and their subfolders, ignores a change to a file
outside them (never downloading it), and items reachable only from a folder removed from
config become removals that are always held.
"""

from __future__ import annotations

import pytest

from drive_fakes import Harness, pull_counts
from kb_sync import store


@pytest.fixture
def h(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_first_pull_copies_the_folder_tree_with_front_matter(h):
    r = h.pull()
    assert r.rc in (0, 1), r  # the oversized Doc fails, which is not a scope matter
    # Top level, subfolder and sub-subfolder files are all in the store.
    for fid in ("doc1", "doc2", "txt1"):
        path = h.store_file("drive", fid)
        assert path.is_file(), fid
    meta, body = store.read_markdown(h.store_file("drive", "doc1"))
    assert list(meta) == ["title", "source", "url", "modified"]
    assert meta["title"] == "Team topologies notes"
    assert meta["source"] == "drive"
    assert meta["url"] == "https://docs.google.com/document/d/doc1/edit"
    assert meta["modified"] == h.drive.files["doc1"]["modifiedTime"]
    assert "Bluebird" in body
    assert h.store_file("drive", "txt1").read_text().count("An Elegant Puzzle") == 1


def test_file_outside_the_allow_list_is_never_read(h):
    h.pull()
    assert not h.store_file("drive", "secret1").exists()
    assert "secret1" not in h.drive.downloads()
    # A later change to it is dropped before any download.
    h.drive.edit_doc("secret1", "# Private diary\n\nnew private entry\n")
    h.drive.add_doc("secret2", "Another private doc", ["personal"], "private\n")
    r = h.pull()
    assert pull_counts(r)["new"] == 0
    assert pull_counts(r)["changed"] == 0
    assert "secret1" not in h.drive.downloads()
    assert "secret2" not in h.drive.downloads()
    assert not list(h.inputs_dir.rglob("secret*"))
    rec = h.records()
    assert rec.get_item("drive", "secret1") is None
    assert rec.get_item("drive", "secret2") is None


def test_new_file_in_a_subfolder_is_picked_up_by_the_changes_feed(h):
    h.pull()
    h.drive.add_doc("doc9", "Calibration guide", ["deep"], "# Calibration guide\n\nUse the ladder.\n")
    r = h.pull()
    assert pull_counts(r)["new"] == 1
    assert "Use the ladder." in h.store_file("drive", "doc9").read_text()


def test_folder_removed_from_config_holds_its_items(h):
    h.set_env(KB_SYNC_DRIVE_FOLDER_IDS="lead,personal")
    h.pull()
    planned, applied = h.ingest()
    assert applied.rc in (0, 1)
    assert h.store_file("drive", "secret1").is_file()
    assert h.lightrag.doc_for("drive__secret1.md") is not None

    # Drop "personal" from the allow-list: its item becomes a held removal.
    h.set_env(KB_SYNC_DRIVE_FOLDER_IDS="lead")
    r = h.pull()
    assert pull_counts(r)["removed"] == 1
    item = h.records().get_item("drive", "secret1")
    assert item.pending == "remove"
    assert item.removal_reason == "out_of_allow_list"

    planned = h.plan()
    f = planned.fields()
    assert f["removals"] == "0" and f["held"] == "1"
    plan = h.plan_json()
    assert plan["removals"] == []
    assert [e["ref"] for e in plan["held"]] == ["drive:secret1"]
    assert plan["held"][0]["reason"] == "out_of_allow_list"

    applied = h.apply(f["plan_id"])
    assert applied.rc == 4
    assert applied.fields()["held"] == "1"
    # Nothing was removed from the knowledge base or the store.
    assert h.lightrag.doc_for("drive__secret1.md") is not None
    assert h.store_file("drive", "secret1").is_file()


def test_out_of_allow_list_stays_held_even_under_the_limit(h):
    h.set_env(KB_SYNC_DRIVE_FOLDER_IDS="lead,personal", KB_SYNC_REMOVAL_LIMIT="100")
    h.pull()
    h.ingest()
    h.set_env(KB_SYNC_DRIVE_FOLDER_IDS="lead")
    h.pull()
    f = h.plan().fields()
    assert (f["removals"], f["held"]) == ("0", "1")


def test_item_still_reachable_from_a_kept_folder_is_not_removed(h):
    # doc1 also sits in "personal"; dropping "personal" leaves it in scope through "lead".
    h.drive.files["doc1"]["parents"] = ["lead", "personal"]
    h.set_env(KB_SYNC_DRIVE_FOLDER_IDS="lead,personal")
    h.pull()
    h.set_env(KB_SYNC_DRIVE_FOLDER_IDS="lead")
    h.pull()
    rec = h.records()
    item = rec.get_item("drive", "doc1")
    assert item.pending != "remove"
    assert rec.locations(item.id) == ["lead"]


def test_folder_moved_out_of_the_tree_holds_its_items(h):
    h.pull()
    h.ingest()
    h.drive.move("deep", ["personal"])
    h.pull()
    item = h.records().get_item("drive", "txt1")
    assert (item.pending, item.removal_reason) == ("remove", "out_of_allow_list")
    f = h.plan().fields()
    assert f["held"] == "1"


def test_folder_moved_into_the_tree_is_walked(h):
    h.pull()
    h.drive.move("personal", ["lead"])
    h.pull()
    assert h.store_file("drive", "secret1").is_file()
    assert h.records().locations(h.records().get_item("drive", "secret1").id) == ["personal"]
