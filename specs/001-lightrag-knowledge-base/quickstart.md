# Quickstart & Validation: LightRAG Local Knowledge Base

**Date**: 2026-08-30
**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)
**Contracts**: [lightrag-api.md](./contracts/lightrag-api.md) · [operational-cli.md](./contracts/operational-cli.md)

This is the run/validation guide — how to bring the system up on the target machine and
prove it works end to end. It is not implementation detail; the scripts and `compose.yaml`
are produced by `/speckit-tasks` → implementation.

---

## Prerequisites (host)

| Requirement | Check | If missing |
|-------------|-------|------------|
| Docker Engine + Compose v2 | `docker compose version` | install `docker`, enable the service, add user to `docker` group |
| Docker daemon running | `docker info` | `systemctl start docker` |
| Ollama running on host | `curl -s http://localhost:11434/api/tags` | install/start `ollama` |
| CPU LLM model pulled | `ollama list \| grep "$LLM_MODEL"` | `ollama pull qwen2.5:3b-instruct` |
| Embedding model pulled | `ollama list \| grep "$EMBEDDING_MODEL"` | `ollama pull bge-m3` |
| `curl`, `jq`, `tar` | `command -v curl jq tar` | install via `pacman` |
| No GPU required | — | — |

> Ollama may stay on its default `127.0.0.1:11434` bind — the container uses the host
> network namespace and reaches it directly (research Decision 2). No Ollama
> reconfiguration is needed for the default setup.

---

## First-time setup (target: under 30 minutes — SC-001)

```bash
# 1. From a clean checkout of this repo:
cp .env.example .env
chmod 0644 .env

# 2. Edit .env — confirm/adjust:
#    LIGHTRAG_IMAGE_TAG   (pinned tag)
#    LLM_MODEL / EMBEDDING_MODEL / EMBEDDING_DIM   (must match a pulled Ollama model)
#    HOST=127.0.0.1  PORT=9621   (leave HOST at loopback)
$EDITOR .env

# 3. Bring the service up (pulls the image, starts the container, waits for /health):
./scripts/start.sh

# 4. Verify health (LightRAG + Ollama reachability + models + storage writable):
./scripts/health.sh
```

Expected: `start.sh` prints `http://127.0.0.1:9621`; `health.sh` reports every check
`OK` and overall `HEALTHY`.

Open `http://127.0.0.1:9621` in a browser for the Web UI; `http://127.0.0.1:9621/docs`
for the API reference.

---

## Everyday operations

| Task | Command | Notes |
|------|---------|-------|
| Start | `./scripts/start.sh` | idempotent |
| Stop | `./scripts/stop.sh` | KB retained on disk |
| Restart | `./scripts/restart.sh` | add `--recreate` to recreate the container |
| Health | `./scripts/health.sh` | add `--json` for scripting |
| Logs | `./scripts/logs.sh` | `--tail N`, `-f` |
| Ingest | drop files in `data/inputs/`, then `./scripts/ingest.sh` | `.txt` / `.md` / text `.pdf`; unchanged files skipped |
| Ingest (ad-hoc) | Web UI upload panel | no file-watcher; nothing auto-ingests |
| Query | Web UI, or `POST http://127.0.0.1:9621/query` | see contract §6 |
| Backup | `./scripts/backup.sh` | writes `data/backups/kb-<ts>.tar.gz` + manifest |
| Restore | `./scripts/restore.sh data/backups/kb-<ts>.tar.gz` | refuses to overwrite a live KB without `--force` + confirm |
| Update | `./scripts/update.sh` | backs up first, then pulls + recreates |

---

## End-to-end validation (FR-031, SC-011, SC-012)

Run the full smoke test on the target machine:

```bash
./scripts/smoke-test.sh
```

It executes contract tests **C1–C8** ([lightrag-api.md](./contracts/lightrag-api.md#contract-tests-executed-by-smoke-testsh))
in order and prints a PASS/FAIL table. The flow it proves:

| Step | Demonstrates | Spec |
|------|--------------|------|
| 1 | Service starts; `/health` → 200 | FR-019, SC-001 |
| 2 | Web UI reachable (`GET /` → 200 HTML) | FR-008, SC-011 |
| 3 | API reachable (`GET /docs` → 200) | FR-009, SC-011 |
| 4 | A Markdown **and** a PDF fixture ingest to `processed` | FR-002, FR-003, SC-011 |
| 5 | A query returns the fixture-only fact **with a source reference** | FR-010, FR-011, SC-003 |
| 6 | After container recreation, the same query returns the same fact with **0** re-ingestion | FR-007, FR-012, SC-004, SC-005, SC-006 |
| 7 | The service is not reachable from a non-loopback address | FR-028, SC-010 |
| 8 | Backup → wipe → restore returns identical query results | FR-015, FR-016, SC-007 |

All steps run against a small CPU-only Ollama model, satisfying SC-012.

Manual spot-check for retrieval quality (SC-003): after ingesting your real documents, ask
~10 questions whose answers are in specific documents; expect ≥ 8/10 correct and
source-referenced.

---

## Recovery scenarios (documented; see `docs/`)

| Scenario | Procedure | Doc |
|----------|-----------|-----|
| Container lost / corrupted | `./scripts/start.sh` re-creates it; bind-mounted KB is intact | `OPERATIONS.md` |
| Whole checkout lost (disk failure) | fresh `git clone` → `cp .env.example .env` (re-enter values) → `restore.sh <latest backup>` → `start.sh` | `OPERATIONS.md`, `ARCHITECTURE.md` |
| Ollama unreachable | `health.sh` names it; check `ollama serve`, `LLM_BINDING_HOST`; bridge-networking fallback | `TROUBLESHOOTING.md` |
| Model not installed | `health.sh` names it; `ollama pull <model>` | `TROUBLESHOOTING.md` |
| Need a different query LLM | edit `LLM_MODEL` in `.env` → `restart.sh` (no re-ingestion) | `MODEL_SELECTION.md` |
| Need a different embedding model | edit `EMBEDDING_MODEL` + `EMBEDDING_DIM` → re-index (re-scan sources) | `MODEL_SELECTION.md` |
| Interrupted ingestion | re-run `./scripts/ingest.sh`; LightRAG resumes unfinished documents | `TROUBLESHOOTING.md` |
| Disk filled during ingest/backup | free space; re-run the operation (scripts fail cleanly, no partial artifact) | `TROUBLESHOOTING.md` |

---

## What this deployment deliberately does **not** do

- No authentication — access control is loopback binding only (clarification Q4). Do not
  change `HOST` away from `127.0.0.1` without first setting `LIGHTRAG_API_KEY`.
- No background file-watcher, no ingest-on-startup — ingestion is always explicit.
- No automatic removal of KB content when a file leaves `data/inputs/`.
- No scheduled backups or ingestion — the owner triggers these.
- No external database or sidecar services.
