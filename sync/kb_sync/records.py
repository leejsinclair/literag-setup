"""kb-sync records store (spec 002 T023; data-model.md; research.md R11).

One SQLite file, `<records_dir>/kb-sync.db` (stdlib sqlite3, WAL off), plus one JSON run log
per run under `<records_dir>/runs/<run_id>.json`.

Rules this module enforces:

- One record per (source, source_id). Identity survives renames and moves.
- Every write method commits before it returns, so an interrupted sync keeps every item it
  finished writing and replays the rest idempotently (edge case "Sync interrupted").
- Run logs are append-only. `finish_run` refuses to overwrite a log that exists.
- A removal entry with outcome `removed` must name the backup taken before it (SC-008).
- No secret is ever written here (FR-004). Callers pass titles and reasons, never tokens.

The schema version is stored in SQLite's `user_version` header field, so `backup.sh` can read
it from the file header without a sqlite3 binary.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets as _secrets
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import KbSyncError

SCHEMA_VERSION = 1


class RecordsError(KbSyncError):
    """The records store refused a write or could not read what was asked for. Exit 1."""


@dataclasses.dataclass
class Item:
    """One upstream item (data-model.md → Item record)."""

    id: int
    source: str
    source_id: str
    title: str | None = None
    url: str | None = None
    modified: str | None = None
    fingerprint: str | None = None
    store_path: str | None = None
    staged_name: str | None = None
    doc_id: str | None = None
    status: str | None = None
    pending: str = "none"
    held: bool = False
    removal_reason: str | None = None
    duplicate_of: int | None = None
    failure: dict | None = None
    mime: str | None = None
    topics: list[str] = dataclasses.field(default_factory=list)
    disposition: str = "ingest"
    size_bytes: int | None = None
    upstream_md5: str | None = None
    ingested_fingerprint: str | None = None
    ingested_at: str | None = None
    last_cited_at: str | None = None


# Columns stored directly on the `items` row (everything except id/source/source_id/topics).
_ITEM_COLUMNS = tuple(
    f.name for f in dataclasses.fields(Item) if f.name not in {"id", "source", "source_id", "topics"}
)
_WRITABLE = frozenset(_ITEM_COLUMNS) | {"topics"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source               TEXT NOT NULL,
    source_id            TEXT NOT NULL,
    title                TEXT,
    url                  TEXT,
    modified             TEXT,
    fingerprint          TEXT,
    store_path           TEXT,
    staged_name          TEXT,
    doc_id               TEXT,
    status               TEXT,
    pending              TEXT NOT NULL DEFAULT 'none',
    held                 INTEGER NOT NULL DEFAULT 0,
    removal_reason       TEXT,
    duplicate_of         INTEGER,
    failure              TEXT,
    mime                 TEXT,
    disposition          TEXT NOT NULL DEFAULT 'ingest',
    size_bytes           INTEGER,
    upstream_md5         TEXT,
    ingested_fingerprint TEXT,
    ingested_at          TEXT,
    last_cited_at        TEXT,
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS items_staged_name ON items (staged_name);
CREATE INDEX IF NOT EXISTS items_status_pending ON items (status, pending);
CREATE TABLE IF NOT EXISTS item_topics (
    item_id INTEGER NOT NULL REFERENCES items (id),
    topic   TEXT NOT NULL,
    PRIMARY KEY (item_id, topic)
);
CREATE TABLE IF NOT EXISTS locations (
    item_id           INTEGER NOT NULL REFERENCES items (id),
    container_id      TEXT NOT NULL,
    container_path    TEXT,
    allow_root        TEXT,
    first_seen_run_id TEXT,
    PRIMARY KEY (item_id, container_id)
);
CREATE TABLE IF NOT EXISTS sources (
    source_key         TEXT PRIMARY KEY,
    config_fingerprint TEXT,
    cursor             TEXT,
    auth_state         TEXT,
    last_pull_at       TEXT,
    last_pull_run_id   TEXT,
    last_error         TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    exit_code   INTEGER,
    plan_id     TEXT,
    backup_ref  TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL REFERENCES runs (run_id),
    item_id INTEGER,
    title   TEXT,
    source  TEXT,
    action  TEXT NOT NULL,
    reason  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS removals (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL REFERENCES runs (run_id),
    item_id    INTEGER,
    title      TEXT,
    source     TEXT,
    doc_id     TEXT,
    reason     TEXT,
    backup_ref TEXT,
    outcome    TEXT NOT NULL CHECK (outcome IN ('removed', 'held', 'failed')),
    at         TEXT NOT NULL,
    CHECK (outcome <> 'removed' OR (backup_ref IS NOT NULL AND backup_ref <> ''))
);
CREATE TABLE IF NOT EXISTS notebooks (
    topic          TEXT PRIMARY KEY,
    notebook_id    TEXT,
    title          TEXT,
    source_limit   INTEGER NOT NULL DEFAULT 50,
    created_at     TEXT,
    last_push_at   TEXT,
    last_review_at TEXT
);
CREATE TABLE IF NOT EXISTS notebook_sources (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    topic              TEXT NOT NULL REFERENCES notebooks (topic),
    item_id            INTEGER NOT NULL REFERENCES items (id),
    notebook_source_id TEXT,
    kind               TEXT CHECK (kind IN ('text', 'pdf', 'url')),
    pushed_fingerprint TEXT,
    pushed_at          TEXT,
    state              TEXT NOT NULL DEFAULT 'active'
                       CHECK (state IN ('active', 'pending_manual_drop', 'dropped'))
);
CREATE UNIQUE INDEX IF NOT EXISTS notebook_sources_live
    ON notebook_sources (topic, item_id) WHERE state <> 'dropped';
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Records:
    """The kb-sync records store. Every write method commits before returning."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.records_dir = self.path.parent
        self.runs_dir = self.records_dir / "runs"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = DELETE")
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('records_version', '0')"
            )
            if self._conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        self._conn.close()

    # --- Items --------------------------------------------------------------

    @property
    def records_version(self) -> int:
        """A counter bumped on every item commit (the ingest plan's staleness guard)."""
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'records_version'").fetchone()
        return int(row["value"])

    def _bump_version(self) -> None:
        self._conn.execute(
            "UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key = 'records_version'"
        )

    @staticmethod
    def _check_fields(fields: dict[str, Any]) -> None:
        unknown = set(fields) - _WRITABLE
        if unknown:
            raise RecordsError(f"unknown item field(s): {', '.join(sorted(unknown))}")

    @staticmethod
    def _encode(name: str, value: Any) -> Any:
        if name == "failure":
            return None if value is None else json.dumps(value, sort_keys=True)
        if name == "held":
            return 1 if value else 0
        return value

    def _row_to_item(self, row: sqlite3.Row) -> Item:
        values: dict[str, Any] = {name: row[name] for name in _ITEM_COLUMNS}
        values["held"] = bool(values["held"])
        values["failure"] = json.loads(values["failure"]) if values["failure"] else None
        topics = [
            r["topic"]
            for r in self._conn.execute(
                "SELECT topic FROM item_topics WHERE item_id = ? ORDER BY rowid", (row["id"],)
            )
        ]
        return Item(id=row["id"], source=row["source"], source_id=row["source_id"], topics=topics, **values)

    def _write_fields(self, item_id: int, fields: dict[str, Any]) -> None:
        cols = [k for k in fields if k != "topics"]
        if cols:
            assignments = ", ".join(f"{c} = ?" for c in cols)
            self._conn.execute(
                f"UPDATE items SET {assignments} WHERE id = ?",
                [self._encode(c, fields[c]) for c in cols] + [item_id],
            )
        if "topics" in fields:
            self._conn.execute("DELETE FROM item_topics WHERE item_id = ?", (item_id,))
            seen: list[str] = []
            for topic in fields["topics"] or []:
                if topic not in seen:
                    seen.append(topic)
                    self._conn.execute(
                        "INSERT INTO item_topics (item_id, topic) VALUES (?, ?)", (item_id, topic)
                    )

    def upsert_item(self, source: str, source_id: str, **fields: Any) -> Item:
        """Create or update the one record for (source, source_id). Unpassed fields are kept."""
        self._check_fields(fields)
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO items (source, source_id) VALUES (?, ?)", (source, source_id)
            )
            item_id = self._conn.execute(
                "SELECT id FROM items WHERE source = ? AND source_id = ?", (source, source_id)
            ).fetchone()["id"]
            self._write_fields(item_id, fields)
            self._bump_version()
        return self.get_by_id(item_id)

    def update_item(self, item_id: int, **fields: Any) -> Item:
        """Change only the named fields of an existing record."""
        self._check_fields(fields)
        with self._conn:
            if self._conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
                raise RecordsError(f"no item with id {item_id}")
            self._write_fields(item_id, fields)
            self._bump_version()
        return self.get_by_id(item_id)

    def get_item(self, source: str, source_id: str) -> Item | None:
        row = self._conn.execute(
            "SELECT * FROM items WHERE source = ? AND source_id = ?", (source, source_id)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def get_by_id(self, item_id: int) -> Item | None:
        row = self._conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def get_by_staged_name(self, name: str) -> Item | None:
        row = self._conn.execute(
            "SELECT * FROM items WHERE staged_name = ? ORDER BY id LIMIT 1", (name,)
        ).fetchone()
        return self._row_to_item(row) if row else None

    def items(self, *, status: str | None = None, pending: str | None = None) -> list[Item]:
        clauses, params = [], []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if pending is not None:
            clauses.append("pending = ?")
            params.append(pending)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM items{where} ORDER BY id", params).fetchall()
        return [self._row_to_item(r) for r in rows]

    # --- Locations ----------------------------------------------------------

    def set_locations(self, item_id: int, locations: list[str]) -> None:
        """Rewrite the item's locations as a set (container IDs)."""
        with self._conn:
            self._conn.execute("DELETE FROM locations WHERE item_id = ?", (item_id,))
            for container_id in dict.fromkeys(locations):
                self._conn.execute(
                    "INSERT INTO locations (item_id, container_id) VALUES (?, ?)",
                    (item_id, container_id),
                )
            self._bump_version()

    def locations(self, item_id: int) -> list[str]:
        return [
            r["container_id"]
            for r in self._conn.execute(
                "SELECT container_id FROM locations WHERE item_id = ? ORDER BY rowid", (item_id,)
            )
        ]

    # --- Source cursors -----------------------------------------------------

    def get_cursor(self, source_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT cursor FROM sources WHERE source_key = ?", (source_key,)
        ).fetchone()
        return row["cursor"] if row else None

    def set_cursor(self, source_key: str, value: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO sources (source_key, cursor) VALUES (?, ?) "
                "ON CONFLICT (source_key) DO UPDATE SET cursor = excluded.cursor",
                (source_key, value),
            )

    # --- Runs ---------------------------------------------------------------

    def start_run(self, kind: str) -> str:
        """Open a run. The id is a UTC timestamp plus a short random suffix."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        with self._conn:
            while True:
                run_id = f"{stamp}-{_secrets.token_hex(3)}"
                try:
                    self._conn.execute(
                        "INSERT INTO runs (run_id, kind, started_at) VALUES (?, ?, ?)",
                        (run_id, kind, _now()),
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
        return run_id

    def _require_open_run(self, run_id: str) -> sqlite3.Row:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RecordsError(f"no run with id {run_id}")
        if row["finished_at"] is not None:
            raise RecordsError(f"run {run_id} is already finished; run logs are append-only")
        return row

    def log_item(self, run_id: str, *, item_id: int | None, title: str | None, source: str | None,
                 action: str, reason: str = "") -> None:
        with self._conn:
            self._require_open_run(run_id)
            self._conn.execute(
                "INSERT INTO run_items (run_id, item_id, title, source, action, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, item_id, title, source, action, reason or ""),
            )

    def log_removal(self, run_id: str, *, item_id: int | None, title: str | None,
                    source: str | None, doc_id: str | None, reason: str | None,
                    backup_ref: str | None, outcome: str) -> None:
        """Record one removal entry. `removed` without a backup_ref is refused (SC-008)."""
        if outcome not in {"removed", "held", "failed"}:
            raise RecordsError(f"invalid removal outcome: {outcome}")
        if outcome == "removed" and not backup_ref:
            raise RecordsError(
                f"refusing to record removal of {title!r} without a backup reference (SC-008)"
            )
        with self._conn:
            self._require_open_run(run_id)
            self._conn.execute(
                "INSERT INTO removals (run_id, item_id, title, source, doc_id, reason, backup_ref, "
                "outcome, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, item_id, title, source, doc_id, reason, backup_ref or None, outcome, _now()),
            )

    def finish_run(self, run_id: str, *, exit_code: int, counts: dict, per_source: list,
                   plan_id: str | None = None, backup_ref: str | None = None) -> Path:
        """Close the run and write `<records_dir>/runs/<run_id>.json`. Never overwrites."""
        run = self._require_open_run(run_id)
        final = self.runs_dir / f"{run_id}.json"
        if final.exists():
            raise RecordsError(f"run log {final} already exists; run logs are append-only")
        finished_at = _now()
        items = [
            {"item_id": r["item_id"], "title": r["title"], "source": r["source"],
             "action": r["action"], "reason": r["reason"]}
            for r in self._conn.execute(
                "SELECT * FROM run_items WHERE run_id = ? ORDER BY seq", (run_id,)
            )
        ]
        removals = [
            {"item_id": r["item_id"], "title": r["title"], "source": r["source"],
             "doc_id": r["doc_id"], "reason": r["reason"], "backup_ref": r["backup_ref"],
             "outcome": r["outcome"], "at": r["at"]}
            for r in self._conn.execute(
                "SELECT * FROM removals WHERE run_id = ? ORDER BY seq", (run_id,)
            )
        ]
        log = {
            "run_id": run_id,
            "kind": run["kind"],
            "started_at": run["started_at"],
            "finished_at": finished_at,
            "exit_code": exit_code,
            "plan_id": plan_id,
            "backup_ref": backup_ref,
            "counts": dict(counts),
            "per_source": list(per_source),
            "items": items,
            "removals": removals,
        }
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{run_id}.", suffix=".tmp", dir=self.runs_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(log, fh, indent=2, sort_keys=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            try:
                # A hard link fails if the target exists, so a concurrent writer cannot clobber it.
                os.link(tmp, final)
            except FileExistsError:
                raise RecordsError(
                    f"run log {final} already exists; run logs are append-only"
                ) from None
        finally:
            os.unlink(tmp)
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, exit_code = ?, plan_id = ?, backup_ref = ? "
                "WHERE run_id = ?",
                (finished_at, exit_code, plan_id, backup_ref, run_id),
            )
        return final

    def run_log(self, run_id: str) -> dict:
        path = self.runs_dir / f"{run_id}.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RecordsError(f"no run log for {run_id}") from None
