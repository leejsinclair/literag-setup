"""T013: the kb-sync records store (FR-008, SC-008, edge case "Sync interrupted").

Covers `kb_sync.records` as pinned in the Phase 2 interface sheet:

- one record per (source, source_id) holds every FR-008 field,
- each item change commits on its own, so an interrupted sync keeps what it wrote,
- run logs under `<records_dir>/runs/` are append-only,
- a removal entry with outcome=removed and no backup_ref is rejected (SC-008).

The module is imported inside a fixture, so this file collects before
`kb_sync.records` exists and each test fails with ModuleNotFoundError until T023.
Tests use the Records API only. They never read SQLite rows directly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SYNC_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def records_mod():
    import kb_sync.records as mod  # noqa: PLC0415 - deliberate late import (test-first)

    return mod


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    records_dir = tmp_path / "kb-sync"
    records_dir.mkdir()
    return records_dir / "kb-sync.db"


@pytest.fixture
def records(records_mod, db_path):
    return records_mod.Records(db_path)


FULL_FIELDS = {
    "title": "Hiring principles",
    "url": "https://docs.google.com/document/d/abc123/edit",
    "modified": "2026-11-20T09:15:00Z",
    "fingerprint": "f" * 64,
    "store_path": "data/inputs/drive/abc123.md",
    "staged_name": "drive__abc123.md",
    "doc_id": "doc-0001",
    "status": "active",
    "pending": "none",
    "held": False,
    "removal_reason": None,
    "duplicate_of": None,
    "failure": None,
    "mime": "application/vnd.google-apps.document",
    "topics": ["hiring", "leadership"],
}


# --- One record per item, holding every field ------------------------------


def test_upsert_creates_one_record_holding_every_field(records):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)

    assert isinstance(item.id, int)
    assert item.source == "drive"
    assert item.source_id == "abc123"
    for key, value in FULL_FIELDS.items():
        assert getattr(item, key) == value, key

    fetched = records.get_item("drive", "abc123")
    assert fetched == item


def test_item_dataclass_has_every_listed_field(records_mod):
    import dataclasses

    names = {f.name for f in dataclasses.fields(records_mod.Item)}
    expected = {"id", "source", "source_id", *FULL_FIELDS}
    assert expected <= names


def test_upsert_same_identity_updates_the_one_record(records):
    first = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    second = records.upsert_item(
        "drive", "abc123", title="Hiring principles (v2)", fingerprint="e" * 64
    )

    assert second.id == first.id
    assert second.title == "Hiring principles (v2)"
    assert second.fingerprint == "e" * 64
    # Fields not passed on the second upsert are kept.
    assert second.url == FULL_FIELDS["url"]
    assert second.doc_id == FULL_FIELDS["doc_id"]
    assert len(records.items()) == 1


def test_same_source_id_in_different_sources_are_separate_items(records):
    a = records.upsert_item("drive", "same-id", title="Drive copy", status="active")
    b = records.upsert_item("appflowy", "same-id", title="AppFlowy copy", status="active")

    assert a.id != b.id
    assert records.get_item("drive", "same-id").title == "Drive copy"
    assert records.get_item("appflowy", "same-id").title == "AppFlowy copy"
    assert len(records.items()) == 2


def test_failure_record_round_trips(records):
    failure = {
        "stage": "export",
        "reason": "document too large to export (12 MB)",
        "fingerprint": "a" * 64,
        "at": "2026-11-21T03:00:00Z",
    }
    item = records.upsert_item("drive", "big", title="Huge doc", status="active", failure=failure)
    assert records.get_by_id(item.id).failure == failure


def test_lookups_by_id_staged_name_and_filters(records):
    a = records.upsert_item("drive", "a1", **{**FULL_FIELDS, "staged_name": "drive__a1.md"})
    b = records.upsert_item(
        "research",
        "b2",
        title="Notes",
        staged_name="research__b2.md",
        status="stale",
        pending="replace",
    )
    c = records.upsert_item(
        "appflowy",
        "c3",
        title="Gone",
        staged_name="appflowy__c3.md",
        status="active",
        pending="remove",
        held=True,
        removal_reason="deleted_upstream",
    )

    assert records.get_by_id(b.id) == b
    assert records.get_by_staged_name("appflowy__c3.md") == c
    assert records.get_by_staged_name("drive__missing.md") is None
    assert records.get_item("drive", "missing") is None

    assert {i.id for i in records.items(status="active")} == {a.id, c.id}
    assert {i.id for i in records.items(pending="replace")} == {b.id}
    assert {i.id for i in records.items(status="active", pending="remove")} == {c.id}
    assert records.get_by_id(c.id).held is True


def test_update_item_changes_only_named_fields(records):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    updated = records.update_item(item.id, status="removed", doc_id=None, pending="none")

    assert updated.id == item.id
    assert updated.status == "removed"
    assert updated.doc_id is None
    assert updated.title == FULL_FIELDS["title"]
    assert records.get_by_id(item.id) == updated


def test_locations_are_rewritten_as_a_set(records):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)

    records.set_locations(item.id, ["folderA", "folderB"])
    assert sorted(records.locations(item.id)) == ["folderA", "folderB"]

    records.set_locations(item.id, ["folderC"])
    assert records.locations(item.id) == ["folderC"]

    records.set_locations(item.id, [])
    assert records.locations(item.id) == []


def test_source_cursor_round_trips_and_persists(records_mod, records, db_path):
    assert records.get_cursor("drive") is None
    records.set_cursor("drive", "token-1")
    records.set_cursor("drive", "token-2")
    assert records.get_cursor("drive") == "token-2"
    assert records.get_cursor("appflowy") is None

    reopened = records_mod.Records(db_path)
    assert reopened.get_cursor("drive") == "token-2"


# --- Each item change commits on its own ("Sync interrupted") --------------


def test_each_write_is_visible_to_a_second_connection_immediately(records_mod, records, db_path):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    other = records_mod.Records(db_path)
    assert other.get_item("drive", "abc123") == item

    records.update_item(item.id, title="Renamed")
    assert other.get_by_id(item.id).title == "Renamed"

    records.set_cursor("drive", "token-9")
    assert other.get_cursor("drive") == "token-9"


def test_interrupted_sync_keeps_items_committed_before_the_crash(records_mod, db_path):
    """A process that dies mid-pull keeps every item it finished writing."""
    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(SYNC_DIR)!r})
        from kb_sync.records import Records
        r = Records({str(db_path)!r})
        r.upsert_item("drive", "first", title="First", status="active", pending="ingest")
        r.upsert_item("drive", "second", title="Second", status="active", pending="ingest")
        os._exit(9)  # crash: no close, no cleanup, no atexit
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert proc.returncode == 9, proc.stderr

    after = records_mod.Records(db_path)
    assert after.get_item("drive", "first").title == "First"
    assert after.get_item("drive", "second").title == "Second"
    assert after.get_cursor("drive") is None


def test_rerunning_an_interrupted_upsert_is_idempotent(records_mod, db_path):
    r1 = records_mod.Records(db_path)
    first = r1.upsert_item("drive", "abc123", **FULL_FIELDS)

    r2 = records_mod.Records(db_path)
    again = r2.upsert_item("drive", "abc123", **FULL_FIELDS)

    assert again == first
    assert len(r2.items()) == 1


# --- Run logs are append-only ----------------------------------------------

COUNTS = {"added": 2, "replaced": 1, "removed": 0, "held": 0, "skipped": 1, "failed": 0}
PER_SOURCE = [
    {"source_key": "drive", "outcome": "ok"},
    {"source_key": "appflowy", "outcome": "auth_expired", "renew_command": "scripts/kb.sh auth appflowy"},
]


def _finish(records, run_id, **overrides):
    kwargs = {"exit_code": 0, "counts": COUNTS, "per_source": PER_SOURCE}
    kwargs.update(overrides)
    return records.finish_run(run_id, **kwargs)


def test_finish_run_writes_a_json_log_under_runs(records, db_path):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    run_id = records.start_run("ingest")
    records.log_item(run_id, item_id=item.id, title=item.title, source="drive", action="added")

    path = _finish(records, run_id)

    assert path == db_path.parent / "runs" / f"{run_id}.json"
    assert path.is_file()
    on_disk = json.loads(path.read_text())

    log = records.run_log(run_id)
    assert log == on_disk
    assert log["exit_code"] == 0
    for key, value in COUNTS.items():
        assert log["counts"][key] == value
    assert [s["source_key"] for s in log["per_source"]] == ["drive", "appflowy"]
    assert log["per_source"][1]["outcome"] == "auth_expired"
    assert any(
        entry["item_id"] == item.id and entry["action"] == "added" and entry["title"] == item.title
        for entry in log["items"]
    )


def test_each_run_gets_its_own_log_and_earlier_logs_are_untouched(records):
    run_a = records.start_run("pull")
    path_a = _finish(records, run_a)
    bytes_a = path_a.read_bytes()
    mtime_a = path_a.stat().st_mtime_ns

    run_b = records.start_run("ingest")
    assert run_b != run_a
    path_b = _finish(records, run_b, exit_code=1)

    assert path_b != path_a
    assert path_a.read_bytes() == bytes_a
    assert path_a.stat().st_mtime_ns == mtime_a
    assert records.run_log(run_a)["exit_code"] == 0
    assert records.run_log(run_b)["exit_code"] == 1


def test_finish_run_refuses_to_overwrite_an_existing_log(records):
    from kb_sync import KbSyncError

    run_id = records.start_run("ingest")
    path = _finish(records, run_id)
    original = path.read_bytes()

    with pytest.raises(KbSyncError):
        _finish(records, run_id, exit_code=1, counts={**COUNTS, "failed": 5})

    assert path.read_bytes() == original
    assert records.run_log(run_id)["exit_code"] == 0


# --- SC-008: a removal without a backup is rejected ------------------------


def _removal(records, run_id, item, **overrides):
    kwargs = {
        "item_id": item.id,
        "title": item.title,
        "source": item.source,
        "doc_id": "doc-0001",
        "reason": "deleted_upstream",
        "backup_ref": "kb-20261121T030000Z.tar.gz",
        "outcome": "removed",
    }
    kwargs.update(overrides)
    return records.log_removal(run_id, **kwargs)


@pytest.mark.parametrize("missing", ["", None])
def test_removed_entry_without_backup_ref_is_rejected(records_mod, records, missing):
    from kb_sync import KbSyncError

    assert issubclass(records_mod.RecordsError, KbSyncError)
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    run_id = records.start_run("ingest")

    with pytest.raises(records_mod.RecordsError):
        _removal(records, run_id, item, backup_ref=missing)


def test_removed_entry_with_backup_ref_is_accepted(records):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    run_id = records.start_run("ingest")
    _removal(records, run_id, item)
    _finish(records, run_id, counts={**COUNTS, "removed": 1})


@pytest.mark.parametrize("outcome", ["held", "failed"])
def test_held_or_failed_entry_needs_no_backup_ref(records, outcome):
    item = records.upsert_item("drive", "abc123", **FULL_FIELDS)
    run_id = records.start_run("ingest")
    _removal(records, run_id, item, backup_ref="", outcome=outcome)
