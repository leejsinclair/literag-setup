"""`kb-sync ingest --plan | --apply` (T041; FR-008, FR-010, FR-011; SC-008).

The host script `scripts/kb.sh ingest` runs `--plan`, backs up when the plan removes
anything, waits for LightRAG to come back, then runs `--apply` (plan.md → Calls).

Plan (data-model.md → Ingest plan):
- removals: items with `pending=remove`, plus the old document of each `pending=replace` item
  (reason `replaced`). Both count toward `KB_SYNC_REMOVAL_LIMIT`. Over the limit, every one
  is held. `out_of_allow_list` removals are always held. A held replacement is not staged and
  stays `stale`. `--confirm-removals` releases every held removal. New items are never held.
- stages: items with `pending=ingest`, and replacements whose old document is not held.
- An item whose last failure was for its current content is not retried (`skipped`).

Apply refuses (exit 4) a plan that is not the current one, was applied already, is out of
date with the records, or removes anything without `--backup <kb-YYYYMMDDTHHMMSSZ.tar.gz>`
newer than the plan. Then: delete each removed or replaced document (the client waits for
the pipeline to go idle), stage the new copies, scan, wait, bind each `doc_id`. Every item
state change commits on its own, so a killed run resumes: a replacement whose old document
is gone becomes `pending=ingest`, and a staged copy LightRAG already holds is bound
without a second scan.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import KbSyncError, Refused, store
from .config import log
from .lightrag import LightRAG
from .records import Records

PLAN_FILE = "plan.json"
ALWAYS_HELD = {"out_of_allow_list"}
BACKUP_RE = re.compile(r"^kb-(\d{8}T\d{6}Z)\.tar\.gz$")
TIME_FMT = "%Y-%m-%dT%H:%M:%SZ"
SUMMARY_KEYS = ("added", "replaced", "removed", "held", "skipped", "failed")


def _now() -> str:
    return datetime.now(UTC).strftime(TIME_FMT)


def _ref(item) -> str:
    return f"{item.source}:{item.source_id}"


# --- store paths (shared with the sources) --------------------------------------


def rel_store_path(cfg, path: Path) -> str:
    """Record a store path relative to the repo root, as `data/inputs/<source>/<id>.<ext>`."""
    return "data/" + Path(path).relative_to(cfg.data_dir).as_posix()


def resolve_store_path(cfg, rel: str) -> Path:
    """Inverse of `rel_store_path`, under the configured data dir."""
    rel = str(rel)
    if rel.startswith("data/"):
        return cfg.data_dir / rel[len("data/"):]
    return Path(rel) if Path(rel).is_absolute() else cfg.data_dir / rel


# --- plan file -------------------------------------------------------------------


def _plan_path(cfg) -> Path:
    return cfg.records_dir / PLAN_FILE


def _write_plan(cfg, plan: dict) -> None:
    path = _plan_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".plan.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_plan(cfg) -> dict | None:
    try:
        return json.loads(_plan_path(cfg).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _known_failure(item) -> bool:
    """LightRAG already failed this exact content; it is not retried until it changes."""
    f = item.failure
    return bool(f) and f.get("stage") == "ingest" and f.get("fingerprint") == item.fingerprint


# --- --plan ----------------------------------------------------------------------


def build_plan(cfg, records: Records, *, confirm: bool) -> dict:
    removals, held, stages, skipped = [], [], [], []
    candidates = []  # (item, reason, doc_id)
    always = []
    for item in records.items(pending="remove"):
        reason = item.removal_reason or "deleted_upstream"
        if reason in ALWAYS_HELD and not confirm:
            always.append((item, reason, item.doc_id))
        else:
            candidates.append((item, reason, item.doc_id))
    replacing = [i for i in records.items(pending="replace") if i.disposition == "ingest"]
    for item in replacing:
        if item.doc_id:
            candidates.append((item, "replaced", item.doc_id))

    over = not confirm and len(candidates) > cfg.removal_limit
    to_hold = always + (candidates if over else [])
    to_remove = [] if over else candidates
    held_ids = {i.id for i, _, _ in to_hold}

    for item, reason, doc_id in to_remove:
        removals.append({"ref": _ref(item), "item_id": item.id, "doc_id": doc_id,
                         "title": item.title, "source": item.source, "reason": reason})
        if item.held:
            records.update_item(item.id, held=False)
    for item, reason, doc_id in to_hold:
        held.append({"ref": _ref(item), "item_id": item.id, "doc_id": doc_id,
                     "title": item.title, "source": item.source, "reason": reason})
        if not item.held:
            records.update_item(item.id, held=True)

    for item in records.items(pending="ingest") + replacing:
        if item.disposition != "ingest" or item.id in held_ids:
            continue
        action = "replace" if item.pending == "replace" and item.doc_id else "add"
        stages.append({"ref": _ref(item), "item_id": item.id, "action": action,
                       "staged_name": item.staged_name, "fingerprint": item.fingerprint,
                       "old_doc_id": item.doc_id if action == "replace" else None})
    for item in records.items(status="stale", pending="none"):
        if item.disposition == "ingest" and _known_failure(item):
            skipped.append({"ref": _ref(item), "item_id": item.id, "title": item.title,
                            "source": item.source, "reason": item.failure.get("reason", "")})
    stages.sort(key=lambda s: s["item_id"])

    stamp = datetime.now(UTC)
    return {
        "plan_id": f"{stamp.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}",
        "created_at": stamp.strftime(TIME_FMT),
        "records_version": records.records_version,
        "removal_limit": cfg.removal_limit,
        "confirm_removals": bool(confirm),
        "removals": removals,
        "stages": stages,
        "held": held,
        "skipped": skipped,
        "applied_at": None,
    }


def plan(cfg, args) -> int:
    records = Records(cfg.records_dir / "kb-sync.db")
    p = build_plan(cfg, records, confirm=bool(args.confirm_removals))
    _write_plan(cfg, p)
    for e in p["held"]:
        log(f"held: {e['title']} ({e['source']}): {e['reason']}")
    for e in p["skipped"]:
        log(f"skipped: {e['title']} ({e['source']}): earlier failure not retried: {e['reason']}")
    log(f"plan {p['plan_id']}: stages={len(p['stages'])} removals={len(p['removals'])} "
        f"held={len(p['held'])}")
    print(f"plan_id={p['plan_id']} removals={len(p['removals'])} held={len(p['held'])}")
    return 0


# --- --apply ---------------------------------------------------------------------


def _check_backup(p: dict, backup: str | None) -> None:
    if not backup:
        raise Refused(f"plan {p['plan_id']} removes {len(p['removals'])} document(s); "
                      "refusing without --backup <archive> taken after the plan")
    m = BACKUP_RE.match(Path(backup).name)
    if not m:
        raise Refused(f"--backup {backup} is not a backup.sh archive name (kb-<timestamp>.tar.gz)")
    taken = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    created = datetime.strptime(p["created_at"], TIME_FMT).replace(tzinfo=UTC)
    if taken <= created:
        raise Refused(f"backup {Path(backup).name} is not newer than plan {p['plan_id']} "
                      f"({p['created_at']}); refusing to remove anything")


def _guard(cfg, records: Records, args) -> dict:
    p = _read_plan(cfg)
    if p is None or p.get("plan_id") != args.apply:
        current = p.get("plan_id") if p else "none"
        raise Refused(f"plan {args.apply} is not the current plan ({current}); re-run scripts/kb.sh ingest")
    if p.get("applied_at"):
        raise Refused(f"plan {p['plan_id']} was already applied at {p['applied_at']}")
    if p.get("records_version") != records.records_version:
        raise Refused(f"records changed since plan {p['plan_id']} was made; re-run scripts/kb.sh ingest")
    if p["removals"]:
        _check_backup(p, args.backup)
    return p


class _Apply:
    def __init__(self, cfg, records: Records, lr: LightRAG, run_id: str, backup: str | None):
        self.cfg, self.records, self.lr, self.run_id, self.backup = cfg, records, lr, run_id, backup
        self.counts = dict.fromkeys(SUMMARY_KEYS, 0)

    def fail(self, item, stage: str, reason: str, **fields) -> None:
        self.counts["failed"] += 1
        self.records.update_item(item.id, failure={"stage": stage, "reason": reason,
                                                   "fingerprint": item.fingerprint, "at": _now()},
                                 **fields)
        self.records.log_item(self.run_id, item_id=item.id, title=item.title, source=item.source,
                              action="failed", reason=reason)
        log(f"failed: {item.title} ({item.source}): {reason}")

    def remove(self, entry: dict) -> None:
        item = self.records.get_by_id(entry["item_id"])
        replaced = entry["reason"] == "replaced"
        # Resume safety: only delete what the record still says LightRAG holds.
        if item is None or item.doc_id != entry["doc_id"]:
            return
        if not replaced and item.pending != "remove":
            return
        if entry["doc_id"]:
            try:
                self.lr.delete(entry["doc_id"])
            except KbSyncError as e:
                if getattr(e, "exit_code", 1) == 3:
                    raise
                self.counts["failed"] += 1
                self.records.log_removal(self.run_id, item_id=item.id, title=item.title,
                                         source=item.source, doc_id=entry["doc_id"],
                                         reason=entry["reason"], backup_ref=self.backup,
                                         outcome="failed")
                log(f"failed: removing {item.title} ({item.source}): {e}")
                return
            self.records.log_removal(self.run_id, item_id=item.id, title=item.title,
                                     source=item.source, doc_id=entry["doc_id"],
                                     reason=entry["reason"], backup_ref=self.backup,
                                     outcome="removed")
        if replaced:
            # The old version is gone; the item now needs only its new copy ingested.
            self.records.update_item(item.id, doc_id=None, pending="ingest",
                                     ingested_fingerprint=None)
            return
        fields = {"status": "removed", "pending": "none", "held": False, "doc_id": None,
                  "ingested_fingerprint": None}
        self.records.update_item(item.id, **fields)
        if item.store_path:
            path = resolve_store_path(self.cfg, item.store_path)
            if path.exists():
                moved = store.move_to_removed(path)
                self.records.update_item(item.id, store_path=rel_store_path(self.cfg, moved))
        self.records.log_item(self.run_id, item_id=item.id, title=item.title, source=item.source,
                              action="removed", reason=entry["reason"])
        self.counts["removed"] += 1

    def bind(self, item, doc: dict, action: str) -> None:
        if doc.get("status") == "failed":
            reason = doc.get("error_msg") or "LightRAG reported the document as failed"
            # Keep the failed doc_id so a later change deletes it before re-scanning.
            self.fail(item, "ingest", reason, doc_id=doc.get("id"), status="stale", pending="none")
            return
        self.records.update_item(item.id, doc_id=doc["id"], status="active", pending="none",
                                 failure=None, ingested_fingerprint=item.fingerprint,
                                 ingested_at=_now())
        key = "replaced" if action == "replace" else "added"
        self.counts[key] += 1
        self.records.log_item(self.run_id, item_id=item.id, title=item.title, source=item.source,
                              action=key)

    def stage_and_scan(self, stages: list[dict]) -> None:
        staged = []
        for s in stages:
            item = self.records.get_by_id(s["item_id"])
            if item is None or item.disposition != "ingest":
                continue
            if item.pending not in ("ingest", "replace") or item.doc_id:
                continue  # a replacement whose delete failed keeps its old document
            existing = self.lr.find_doc(item.staged_name)
            if existing and existing.get("status") in ("processed", "failed"):
                self.bind(item, existing, s["action"])  # resumed: LightRAG already has it
                continue
            path = resolve_store_path(self.cfg, item.store_path)
            if not path.is_file():
                self.fail(item, "ingest", f"store file missing: {item.store_path}")
                continue
            store.stage(self.cfg.inputs_dir, path)
            staged.append((item, s["action"]))
        if not staged:
            return
        track_id = self.lr.scan()
        if track_id:
            self.lr.wait_track(track_id)
        for item, action in staged:
            doc = self.lr.find_doc(item.staged_name)
            if doc is None:
                self.fail(item, "ingest", f"LightRAG did not pick up {item.staged_name}")
            elif doc.get("status") not in ("processed", "failed"):
                self.fail(item, "ingest", f"LightRAG left {item.staged_name} {doc.get('status')}")
            else:
                self.bind(item, doc, action)


def apply(cfg, args) -> int:
    records = Records(cfg.records_dir / "kb-sync.db")
    p = _guard(cfg, records, args)
    lr = LightRAG(cfg.lightrag_url, timeout=cfg.ingest_timeout)
    lr.health()
    backup = Path(args.backup).name if args.backup else None
    run_id = records.start_run("ingest")
    a = _Apply(cfg, records, lr, run_id, backup)

    for entry in p["removals"]:
        a.remove(entry)
    for entry in p["held"]:
        a.counts["held"] += 1
        records.log_removal(run_id, item_id=entry["item_id"], title=entry["title"],
                            source=entry["source"], doc_id=entry["doc_id"],
                            reason=entry["reason"], backup_ref=backup, outcome="held")
        records.log_item(run_id, item_id=entry["item_id"], title=entry["title"],
                         source=entry["source"], action="held", reason=entry["reason"])
    for entry in p.get("skipped", []):
        a.counts["skipped"] += 1
        records.log_item(run_id, item_id=entry["item_id"], title=entry["title"],
                         source=entry["source"], action="skipped", reason=entry["reason"])

    a.stage_and_scan(p["stages"])

    p["applied_at"] = _now()
    _write_plan(cfg, p)
    c = a.counts
    code = 1 if c["failed"] else (4 if c["held"] else 0)
    records.finish_run(run_id, exit_code=code, counts=c, per_source=[], plan_id=p["plan_id"],
                       backup_ref=backup)
    if c["held"]:
        log(f"{c['held']} removal(s) held; confirm with scripts/kb.sh ingest --confirm-removals")
    print(" ".join(f"{k}={c[k]}" for k in SUMMARY_KEYS) + f" backup={backup or 'none'}")
    return code


def run(cfg, args) -> int:
    if args.plan:
        return plan(cfg, args)
    return apply(cfg, args)
