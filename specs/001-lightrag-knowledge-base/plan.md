# Implementation Plan: LightRAG Local Knowledge Base

**Branch**: `001-lightrag-knowledge-base` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-lightrag-knowledge-base/spec.md`

## Summary

Stand up the official LightRAG server as a single Docker Compose project on the owner's
EndeavourOS desktop. LightRAG provides the Web UI, REST API, knowledge graph, retrieval
index, and document tracking; this feature only configures and operates it. The container
uses the host network namespace and binds `127.0.0.1:9621` (loopback only), talks to the
host's existing Ollama at `http://localhost:11434` for a small CPU LLM and embedding model,
and keeps all knowledge-base state in host bind mounts under `data/` using LightRAG's
default file-based storage backends (no external database, no supporting services).
Operations (start / stop / restart / health / logs / ingest / backup / restore / update)
are thin `docker compose` wrapper scripts that fail loudly and never destroy data. A
committed end-to-end smoke test proves start → ingest (Markdown + PDF) → query → restart
survival → backup → restore on the real machine. Model choice, provider, directories, and
network binding are all `.env` configuration; the repo plus `.env.example` fully reproduce
the deployment.

See [research.md](./research.md) for the evidence behind every technology decision.

## Technical Context

**Language/Version**: No application code. Operational scripts in POSIX-ish `bash`
(`set -euo pipefail`). Configuration in `.env` (dotenv) and `compose.yaml` (Compose spec).

**Primary Dependencies**:
- LightRAG official image `ghcr.io/hkuds/lightrag:${LIGHTRAG_IMAGE_TAG}` (default variant,
  not `-lite`), pinned to an explicit tag/digest in `.env`.
- Host: Docker Engine + Docker Compose v2; Ollama (host service, default `127.0.0.1:11434`).
- Ollama models: `LLM_MODEL` (default `qwen2.5:3b-instruct`), `EMBEDDING_MODEL` (default
  `bge-m3:latest`, `EMBEDDING_DIM=1024`).
- `curl`, `tar`, `jq` for scripts.

**Storage**: LightRAG default file-based backends — `JsonKVStorage`,
`JsonDocStatusStorage`, `NetworkXStorage`, `NanoVectorDBStorage` — all as plain files under
`data/rag_storage/` (host bind mount). No PostgreSQL/Neo4j/Redis/Milvus/Qdrant/OpenSearch.

**Testing**: `scripts/smoke-test.sh` — on-demand end-to-end validation on the target
machine (not hosted CI). Fixtures in `tests/fixtures/`. Optional `bats` for script unit
checks (nice-to-have, not required).

**Target Platform**: EndeavourOS (Arch-based, rolling release), Linux, x86_64, CPU-only
(no GPU). Single user, single machine.

**Project Type**: Containerised third-party service deployment + operations tooling
(infrastructure repo). No frontend/backend source of our own.

**Performance Goals**: None hard. Interactive single-user query latency acceptable on a
3B CPU model; ingestion throughput is not optimised. Must complete the core
ingest-and-query workflow on CPU (SC-012) and handle several hundred documents without
changing deployment topology (SC-013). Performance is the lowest-ranked operational
priority per the spec.

**Constraints**:
- Web UI + API reachable only from `127.0.0.1`; never `0.0.0.0`; never public Internet
  (FR-028, FR-028a).
- No authentication in the initial implementation; loopback binding is the sole access
  control (clarification Q4).
- Ollama stays on the host, unbundled, and needs no reconfiguration (host networking).
- KB state must survive `docker compose down` and image replacement (FR-012, Principle V).
- Destructive operations require explicit confirmation and never run as a side effect of a
  routine command (FR-013, FR-014).
- Every model/path/port/credential value configurable via `.env`; none hard-coded in
  scripts or `compose.yaml` (Principle IV, VI; FR-024, FR-030).
- All dependency behaviour traceable to official LightRAG docs (Principle VIII).

**Scale/Scope**: Up to a few hundred documents; formats `.txt`, `.md`, text-searchable
`.pdf`; single writer; no concurrency target.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| # | Principle | Assessment | Status |
|---|-----------|------------|--------|
| I | Simplicity Over Infrastructure | One container; file-based storage; host networking removes bridge + gateway mapping; zero supporting services. Every component traces to a spec requirement. | PASS |
| II | Containerised Application | LightRAG + all its deps run in the official image via Compose; host provides only Docker + Ollama, consumed as external dependencies; nothing installed on host. | PASS |
| III | Local-First | No cloud/external APIs; documents and KB stay on disk; Web UI/API bound to `127.0.0.1`; Ollama stays on host loopback. Widening exposure is a documented future decision. | PASS |
| IV | Replaceable AI Models | `LLM_BINDING`/`LLM_MODEL`/`EMBEDDING_BINDING`/`EMBEDDING_MODEL`/`EMBEDDING_DIM` all in `.env`; no model name in code. Swap = edit `.env` + (for embeddings) re-index. | PASS |
| V | Persistent Data Is Sacred | KB in host bind mounts external to the container FS; survives `down` and image replacement; `restore`/`clear` gated by confirmation and never destructive-by-default; `backup` runs automatically before `update`; restore displaces rather than deletes prior state. | PASS |
| VI | Reproducibility | Repo contains `compose.yaml`, `.env.example`, scripts, docs, fixtures. `.env` and `data/**` gitignored. Image tag/digest pinned and recorded. | PASS |
| VII | Operational Clarity | One documented command per operation; `health.sh` verifies Ollama reachability, model presence, and storage writability — not just "process up"; failures name the component. | PASS |
| VIII | Evidence Over Assumption | `research.md` cites current official LightRAG sources for image name, env vars, endpoints, storage backends, parser behaviour; open items flagged for implementation-time confirmation against the pinned version. | PASS |
| IX | Incremental Complexity | Smallest working system first: default storage, no DB, no sidecars, no auth. `research.md` records the concrete signal that would justify a graph/vector DB later. | PASS |
| X | Future Integration Without Premature Implementation | LightRAG REST API preserved as the integration seam and exercised by the smoke test; no MCP/Claude Code integration built, stubbed, or designed around. | PASS |

**Additional Constraints check**: host baseline EndeavourOS ✔; single Compose project ✔;
config via ignored `.env` + committed `.env.example` ✔; ports bound to `127.0.0.1` —
satisfied more strictly by binding the app itself to `127.0.0.1` under host networking,
recorded as the required documented networking decision in `research.md` Decision 2 ✔.

**Result**: PASS — no violations. Complexity Tracking table intentionally empty.

**Post-Phase-1 re-check**: design artifacts introduce no new services, dependencies, or
network exposure; data-model maps 1:1 onto LightRAG's own on-disk artifacts plus one
backup archive; contracts consume the documented REST API only. Constitution Check still
PASS.

## Project Structure

### Documentation (this feature)

```text
specs/001-lightrag-knowledge-base/
├── plan.md              # This file
├── research.md          # Phase 0 — technology decisions with sources
├── data-model.md        # Phase 1 — entities ↔ LightRAG on-disk artifacts + config keys
├── quickstart.md        # Phase 1 — runnable validation scenarios
├── contracts/
│   ├── lightrag-api.md      # REST endpoints the deployment + scripts depend on
│   └── operational-cli.md   # Command contract for each script (args, exit codes, safety)
├── checklists/
│   └── requirements.md      # Spec quality checklist (already validated)
└── tasks.md             # Phase 2 — created by /speckit-tasks, NOT here
```

### Source Code (repository root)

The repository *is* the deployment unit. Layout at repo root:

```text
compose.yaml                 # single Compose project: one `lightrag` service, network_mode: host
.env.example                 # committed template for all configuration (models, paths, binding, image tag)
.env                         # gitignored — real values, readable by uid 1000
.gitignore                   # ignores .env, data/inputs/*, data/rag_storage/*, data/backups/*, data/prompts/*
README.md                    # entry point: what this is, quickstart, links to docs/

data/
├── inputs/       .gitkeep   # source documents dropped here for ingestion  (INPUT_DIR)
├── rag_storage/  .gitkeep   # LightRAG persistent state — THE knowledge base (WORKING_DIR)
├── prompts/      .gitkeep   # optional prompt overrides                     (PROMPT_DIR)
└── backups/      .gitkeep   # backup archives + manifests (not mounted into container)

scripts/
├── lib/
│   └── common.sh            # load .env, strict mode, error helpers, dependency + health probes
├── start.sh                 # docker compose up -d
├── stop.sh                  # docker compose down (bind mounts retained)
├── restart.sh               # docker compose restart (or down+up for full recreation)
├── health.sh                # /health + Ollama reachability + model presence + storage write test
├── logs.sh                  # docker compose logs -f --tail=200
├── ingest.sh                # POST /documents/scan; poll pipeline/track status
├── backup.sh                # quiesce → tar data/rag_storage → manifest → verify → restart
├── restore.sh               # guarded restore; refuses to overwrite non-empty KB without --force + confirm
├── update.sh                # backup → docker compose pull → up -d → health
└── smoke-test.sh            # end-to-end FR-031 / SC-011 / SC-012 validation

docs/
├── ARCHITECTURE.md          # components, data flow, Docker↔Ollama interaction, where data lives, scale ceiling, pinned image digest
├── OPERATIONS.md            # start/stop/restart/health/logs/ingest/backup/restore/update runbook; recover-after-container-loss
├── MODEL_SELECTION.md       # chosen CPU models, how to change LLM vs embedding, EMBEDDING_DIM gotcha, re-index requirement, num_ctx
└── TROUBLESHOOTING.md       # Ollama connectivity (host networking vs bridge fallback), model-not-installed, disk full, interrupted ingestion, restore-onto-live-KB

tests/
└── fixtures/                # committed sample .md and .pdf used by smoke-test.sh (no secrets)
```

**Structure Decision**: Single Compose project rooted at the repository root (not a nested
`lightrag-local/` subdirectory) — the constitution defines the repo as "a single Docker
Compose project under version control in this repository", so an extra nesting level adds
nothing. The user's proposed `data/{inputs,storage,backups}` + `scripts/` + `docs/` shape
is adopted, with `storage/` named `rag_storage/` to match LightRAG's `WORKING_DIR`
convention and a `prompts/` category added because the official image expects a
`PROMPT_DIR` mount. `compose.yaml` is used (Compose auto-discovers it). No application
source tree (`src/`, `backend/`, `frontend/`) exists because we build no application.

## Complexity Tracking

No constitutional violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |
