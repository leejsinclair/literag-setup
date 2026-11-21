# Implementation Plan: Personal knowledge pipeline (`kb-sync`)

**Branch**: `002-kb-sync-knowledge-pipeline` | **Date**: 2026-11-21 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/002-kb-sync-knowledge-pipeline/spec.md`

> **Scale note.** This change touches about 35 files in four areas: a new Python tool under
> `sync/`, Compose and `.env` (two new services), the existing backup, restore and health
> scripts, and the docs. It also amends two of feature 001's data invariants. Watch the
> removal path most closely. It is the only code in the repo that deletes knowledge, and it
> must never run without a backup taken earlier in the same run (SC-008).

## Summary

`kb-sync` is a small Python CLI that runs as a one-shot Compose service behind the
`kb-sync` profile. `scripts/kb.sh` wraps it in the repo's usual idiom. It pulls the
configured Drive folders, an AppFlowy workspace and a local `research/` folder into
Markdown store files under `data/inputs/<source>/`, and it keeps one record per item in a
SQLite file under `data/kb-sync/`. It drives LightRAG only through its REST API: it stages
changed files flat into `data/inputs/` and calls `POST /documents/scan`, and it removes
knowledge through the delete route, always after `scripts/backup.sh` has run in the same
`kb.sh` invocation. `ask` and the MCP tools retrieve context with `POST /query`
(`only_need_context`). They filter it with LightRAG's own rerank binding, pointed at a new
loopback llama.cpp reranker container, or with Ollama scoring as the fallback, and send
only the surviving chunks to Claude. The new dependencies are the Python stack in
`sync/pyproject.toml` (Google API client, `anthropic`, `mcp`, `httpx`, `openpyxl`,
`PyYAML`) and the pinned llama.cpp server image.

## Project Structure

```text
sync/                              # new: the kb-sync tool (build context)
├── Dockerfile                     # python:3.12-slim, uid 1000, `python -m kb_sync`
├── .dockerignore
├── pyproject.toml                 # pinned deps
├── kb_sync/
│   ├── __main__.py, cli.py        # argparse subcommands, exit codes 0-4
│   ├── config.py                  # reads /app/.env (python-dotenv) + secrets dir
│   ├── records.py                 # SQLite: items, locations, sources, runs, removals
│   ├── store.py                   # store files, front-matter, flat staging names
│   ├── lightrag.py                # REST client: scan, track, delete, list, query
│   ├── ingest.py                  # plan → remove → stage → scan → bind doc IDs
│   ├── sources/drive.py           # OAuth, changes feed, export, xlsx → Markdown
│   ├── sources/appflowy.py        # GoTrue sign-in, folder walk, databases
│   ├── sources/appflowy_blocks.py # block JSON → Markdown
│   ├── sources/research.py        # local folder source
│   ├── ask.py, filters.py, citations.py, answer.py   # retrieve → filter → answer
│   ├── mcp_server.py              # kb_search, kb_get_source (stdio)
│   ├── evalset.py                 # FR-025 three-way run
│   ├── packs.py, notebooklm.py, review.py            # Stories 5-6
│   └── nightly.py                 # per-source isolation, run summary
└── tests/                         # pytest, run inside the image; fixtures/
scripts/kb.sh                      # new wrapper (contract: contracts/kb-cli.md)
scripts/backup.sh, restore.sh      # edit: archive/restore data/kb-sync with the KB;
                                   #   backup.sh prunes to BACKUP_RETENTION (FR-033)
scripts/health.sh                  # edit: reranker probe when RERANK_BINDING != null
scripts/lib/common.sh              # edit: shared write_index_stamp, kb-sync defaults
compose.yaml                       # edit: kb-sync (profile) + reranker services
.env, .env.example                 # edit: KB_SYNC_* and RERANK_* sections (byte-identical)
.gitignore                         # edit: data/kb-sync/*, data/models/*, packs/*, secrets/
systemd/kb-sync-nightly.{service,timer}   # new: user timer, installed by hand
data/kb-sync/.gitkeep, data/models/.gitkeep, packs/.gitkeep   # new skeleton
README.md, CONTRIBUTING.md, CLAUDE.md, docs/ARCHITECTURE.md, docs/OPERATIONS.md   # edit
specs/001-lightrag-knowledge-base/data-model.md   # edit: point Invariant 2 at 002's amendment
```

**Structure Decision**: one Python package under `sync/`, built into a local image by
Compose, with no code outside it except `scripts/kb.sh` and edits to existing scripts.
Module boundaries follow the spec's phases so each story lands as its own reviewable change.

## Calls

How `scripts/kb.sh ingest` sequences the run so that every removal follows a backup
(FR-010, SC-008). The container has no Docker socket and no mount of `data/backups/`
(Invariant 4), so the host script owns the backup.

```text
kb.sh ingest [--confirm-removals]
 ├─ service_running / wait_for_health            (common.sh)  → exit 3 if stopped
 ├─ compose --profile kb-sync run --rm kb-sync ingest --plan
 │     ingest.plan()  → data/kb-sync/plan.json {plan_id, created_at, removals[], stages[], held[]}
 ├─ if plan.removals non-empty:  scripts/backup.sh   → prints archive path
 │     (stops + restarts LightRAG; prunes archives and .removed/ to BACKUP_RETENTION)
 ├─ wait_for_health "$READY_TIMEOUT"               → exit 3, nothing removed (FR-034)
 ├─ compose … run --rm kb-sync ingest --apply <plan_id> [--backup <archive name>]
 │     ingest.apply()
 │       ├─ refuse removals unless --backup names an archive newer than plan.created_at
 │       ├─ for each removal: lightrag.delete(doc_id) → poll pipeline_status idle
 │       ├─ store.stage(item) → data/inputs/<source>__<source_id>.md
 │       ├─ lightrag.scan() → poll track_status to terminal
 │       ├─ lightrag.find_doc(file_path) → records.bind(doc_id)
 │       └─ records.log_run(summary, backup_ref)
 ├─ write_index_stamp                             (common.sh, shared with ingest.sh)
 └─ stdout: added / replaced / removed / held / skipped / failed; exit 4 if any held
```

`ask` and `kb_search` share one path: `ask.retrieve()` → `lightrag.query(only_need_context)` →
`ask.parse_chunks()` → `filters.apply()` (reranker | local | none) → `citations.number()` →
`answer.claude()` or `answer.local()`.

## States

An item record's `status` (spec values) and its `pending` action (internal) together make
the sync crash-safe: each step commits before the next starts, so a rerun resumes.

```text
            pull: new                       ingest: scan processed
   (none) ──────────────► stale/ingest ───────────────────────────► active/none
                              ▲                                        │
         pull: content changed│◄───────────────────────────────────────┘
                              │   (pending=replace; old doc_id kept until removed)
   active ── pull: deleted/trashed/out of allow-list ──► active/remove
   active/remove ── ingest within limit, after backup ──► removed/none
   active/remove ── over limit ──► held=true (no change until --confirm-removals)
   stale/replace ── ingest: delete old doc, stage, scan ──► active/none
```

## Constitution Check

*GATE: checked before Phase 0, re-checked after Phase 1.* Constitution v1.1.0 is already
ratified on this branch (commits d82ce62, 66f71d6), and README lines 131-148 list its
exceptions, so FR-001 is met before any feature code.

| # | Principle | Assessment | Status |
|---|-----------|------------|--------|
| I | Simplicity Over Infrastructure | Two components, each tied to a requirement: the one-shot `kb-sync` service (FR-003) and the reranker (FR-020). Records are one SQLite file, using the stdlib driver. No daemon: the MCP server lives for one Claude Code session over stdio. | PASS |
| II | Containerised Application | `kb-sync`, its MCP server and the reranker all run in Compose. The host gets only two systemd user units that call `scripts/kb.sh`, the same pattern as `ollama/ollama.service`. | PASS |
| III | Local-First | Every outbound flow goes to a recorded dependency: Drive and AppFlowy reads, NotebookLM on `notebook push` and `review --apply`, Claude on `ask` and `kb_search`. Only the filtered chunks and the question leave. `--local` sends nothing. The nightly job only pulls. The reranker binds 127.0.0.1. See Complexity Tracking for the web checks and FR-030. | PASS, one justified gap |
| IV | Replaceable AI Models | Claude model, local scoring model (`LLM_MODEL`), reranker model and threshold are all `.env` keys. `answer.py` isolates the Claude call. | PASS |
| V | Persistent Data Is Sacred | Removals only under the 1.1.0 exception: the plan/backup/apply sequence above, a run-log entry per removal, and held removals over `KB_SYNC_REMOVAL_LIMIT`. No removal starts until LightRAG passes its health check after the backup (FR-034). `data/kb-sync/` joins every backup and restore. `ingest.sh` stays additive. Backup retention is a bounded deletion, see Complexity Tracking. | PASS, one justified item |
| VI | Reproducibility | `sync/` with pinned deps, a pinned reranker image tag, both systemd units, and `.env.example` keys are committed. Secrets, records, packs and models are gitignored. | PASS |
| VII | Operational Clarity | One `kb.sh` subcommand per operation, uniform exit codes, an expired sign-in names `kb.sh auth <source>`, a stopped server names `./scripts/start.sh`. `health.sh` probes the reranker. | PASS |
| VIII | Evidence Over Assumption | LightRAG behaviour was read from the pinned v1.5.6 image (research.md R1-R4). This surfaced two corrections to the spec, listed below. Drive, AppFlowy, NotebookLM and reranker facts still to be verified are named Phase 0 checks with a pass condition. | PASS |
| IX | Incremental Complexity | The reranker is the single authorised addition. It is added as its own change in Story 2 and kept only if the FR-025 run favours it. Stories land in spec order. | PASS |
| X | Future Integration | Claude answers and a stdio MCP server, both REST-only and loopback-only. No LightRAG change except rerank config. | PASS |

**Additional Constraints**: EndeavourOS host ✔; one Compose project ✔; config in `.env`
with `.env.example` ✔ (they currently differ at line 97, and the first task restores byte
identity); loopback binding ✔.

**Spec corrections found under Principle VIII** (amended into the spec's Verbatim
Constraints and Clarifications on 2026-11-21):

1. **Removal route.** v1.5.6 has no `DELETE /documents/{doc_id}`. It removes documents
   with `DELETE /documents/delete_document` and the body `{"doc_ids": [...]}`. Bare
   `DELETE /documents` clears the entire knowledge base. The contract uses the real route
   and forbids the bare one.
2. **Store path versus scan.** `POST /documents/scan` reads only the top level of
   `INPUT_DIR` and skips subdirectories, so store files at `data/inputs/<source>/<source_id>.md`
   are never scanned. The pinned store path is kept. Ingest stages a flat copy named
   `<source>__<source_id>.md` in `data/inputs/`, which LightRAG moves into `__parsed__/` after
   processing (research.md R2).

**Post-Phase-1 re-check**: the data model and contracts add no service, volume or exposure
beyond the table above. Two new bind mounts are `data/kb-sync` (records) and `data/models`
(reranker weights). Neither container mounts `data/rag_storage` (kb-sync) or
`data/backups` (either). Still PASS with one justified gap.

## Complexity Tracking

| Violation | Why needed | Simpler alternative rejected |
|---|---|---|
| Outbound flows not in the v1.1.0 table: the review fetches public web URLs the owner listed (Story 6), and FR-030 sends a topic name to Claude's web search. | Both are spec requirements (FR-028, FR-030). Neither sends knowledge-base content. | Silently reading the Claude row as covering it. Instead, add a PATCH amendment (v1.1.1) recording both rows before Story 6 is built. FR-030 stays opt-in behind a flag. |
| `backup.sh` prunes archives beyond the newest 14, and `.removed/` store files likewise (FR-033). Feature 001's backup contract says it "does not delete older backups". | Owner decision on 2026-11-21: unbounded archives fill the disk, and each holds a full copy of `data/rag_storage/`. | Manual pruning, which the owner rejected. The deletion is bounded: it runs only after the new archive verifies, matches only `kb-<timestamp>.tar.gz` and its `.manifest` in the default `data/backups/`, never touches the archive just written, `data/rag_storage/` or `--output` directories, and logs every file. 001's contract gets a pointer to FR-033. |
| Feature 001's Invariant 2 ("only restore.sh can displace KB content") is contradicted by FR-010. | The constitution exception exists, but 001's data model was not updated with it. | Leaving two binding documents in conflict. 002's data model states the amended invariant, and 001's data model gets a pointer to it. |
