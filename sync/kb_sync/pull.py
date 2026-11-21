"""`kb-sync pull`: run each configured source in isolation (contracts/kb-cli.md, FR-032).

A failure or expired sign-in in one source is recorded and reported; the other sources
still pull. Pull never calls LightRAG, so it works with the server stopped.
"""

import importlib
import sys

from . import AuthExpired, KbSyncError
from .config import log
from .records import Records
from .sources import SourceResult

REGISTRY = {
    "drive": "kb_sync.sources.drive",
    "appflowy": "kb_sync.sources.appflowy",
    "research": "kb_sync.sources.research",
}
COLUMNS = ("new", "changed", "removed", "unchanged", "skipped", "failed")


def enabled_sources(cfg):
    on = {
        "drive": bool(cfg.drive_folder_ids),
        "appflowy": bool(cfg.appflowy_workspace_id),
        "research": cfg.research_enabled,
    }
    return [name for name in REGISTRY if on[name]]


def _line(name, counts):
    return f"{name:<10} " + " ".join(f"{k}={counts[k]}" for k in COLUMNS)


def run(cfg, args):
    names = [args.source] if args.source else enabled_sources(cfg)
    records = Records(cfg.records_dir / "kb-sync.db")
    run_id = records.start_run("pull")
    total = dict.fromkeys(COLUMNS, 0)
    per_source = []
    auth_expired = failed = False

    for name in names:
        entry = {"source_key": name, "outcome": "ok", "renew_command": None, "error": None}
        try:
            module = importlib.import_module(REGISTRY[name])
            result = module.pull(cfg, records, run_id)
        except AuthExpired as e:
            auth_expired = True
            entry.update(outcome="auth_expired", renew_command=f"scripts/kb.sh auth {e.renew}", error=str(e))
            print(f"ERROR: {e}", file=sys.stderr)
            result = SourceResult()
        except ModuleNotFoundError as e:
            if e.name != REGISTRY[name]:
                raise
            failed = True
            entry.update(outcome="failed", error=f"source {name} is not built yet")
            print(f"ERROR: source {name} is not built yet", file=sys.stderr)
            result = SourceResult()
        except KbSyncError as e:
            failed = True
            entry.update(outcome="failed", error=str(e))
            print(f"ERROR: {name}: {e}", file=sys.stderr)
            result = SourceResult()

        for title, reason in result.skipped:
            log(f"skipped: {title} ({name}): {reason}")
        for title, reason in result.failed:
            log(f"failed: {title} ({name}): {reason}")
        counts = result.counts()
        if result.failed:
            failed = True
        if entry["outcome"] == "ok" and not any(counts.values()):
            entry["outcome"] = "empty"
        per_source.append(entry)
        for k in COLUMNS:
            total[k] += counts[k]
        print(_line(name, counts))

    print(_line("total", total))
    code = 3 if auth_expired else (1 if failed else 0)
    records.finish_run(run_id, exit_code=code, counts=total, per_source=per_source)
    return code
