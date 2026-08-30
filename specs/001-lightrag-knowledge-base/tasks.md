---
description: "Task list for LightRAG Local Knowledge Base implementation"
---

# Tasks: LightRAG Local Knowledge Base

**Input**: Design documents from `/specs/001-lightrag-knowledge-base/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/lightrag-api.md](./contracts/lightrag-api.md), [contracts/operational-cli.md](./contracts/operational-cli.md), [quickstart.md](./quickstart.md)

**Tests**: No unit-test framework was requested. The one required validation artifact is the
end-to-end `scripts/smoke-test.sh` (FR-031, SC-011) which runs contract tests **C1–C8** from
[contracts/lightrag-api.md](./contracts/lightrag-api.md); it is delivered as an implementation
task (Phase 10), not as TDD scaffolding. An optional `bats` task is listed in Polish.

**Organization**: Tasks are grouped by user story. This repository *is* the deployment unit —
there is no application source tree; deliverables are `compose.yaml`, `.env.example`, `scripts/*.sh`,
and `docs/*.md` at the repo root (see [plan.md](./plan.md) → Project Structure).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different file, no dependency on an incomplete task)
- **[Story]**: US1–US7 map to the user stories in [spec.md](./spec.md)
- Every task names an exact file path

## Path Conventions

All paths are relative to the repository root (`/home/lee/Projects/literag-setup/`).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository skeleton and version-control hygiene.

- [X] T001 Create the directory skeleton with tracked `.gitkeep` files: `data/inputs/.gitkeep`, `data/rag_storage/.gitkeep`, `data/prompts/.gitkeep`, `data/backups/.gitkeep`, `scripts/lib/`, `docs/`, `tests/fixtures/`
- [X] T002 [P] Create `.gitignore` at repo root: ignore `.env`, `data/inputs/*`, `data/rag_storage/*`, `data/backups/*`, `data/prompts/*` while keeping each `.gitkeep` (per research.md Decision 7)
- [X] T003 [P] Create `README.md` skeleton at repo root: one-paragraph description, "see `specs/001-lightrag-knowledge-base/quickstart.md` to run it", placeholder links to `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/MODEL_SELECTION.md`, `docs/TROUBLESHOOTING.md`

**Checkpoint**: Repo structure exists and ignores secrets/data.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The Compose project, the configuration template, and the shared script library.
Nothing in any user story works until these exist.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Create `compose.yaml` at repo root: single `lightrag` service; image `ghcr.io/hkuds/lightrag:${LIGHTRAG_IMAGE_TAG}` (default variant, not `-lite`); `network_mode: host`; bind mounts `./data/inputs:/app/data/inputs`, `./data/rag_storage:/app/data/rag_storage`, `./data/prompts:/app/data/prompts`, `./.env:/app/.env`. **The mounted `.env` is the single authoritative configuration surface**; keep the `environment:` block empty or limited to values Compose itself must interpolate, and never duplicate a key in both places (drift risk — data-model.md Configuration entity); `restart_policy` on-failure; **no `ports:` block**, `./data/backups` **not** mounted (research.md Decisions 2 & 7, Invariant 4)
- [X] T005 [P] Create `.env.example` at repo root: every key from [data-model.md](./data-model.md) "Configuration keys" table with the documented defaults — `LIGHTRAG_IMAGE_TAG`, `HOST=127.0.0.1`, `PORT=9621`, `WORKING_DIR`/`INPUT_DIR`/`PROMPT_DIR`, `LLM_BINDING=ollama`, `LLM_BINDING_HOST=http://localhost:11434`, `LLM_MODEL=qwen2.5:3b-instruct`, `OLLAMA_LLM_NUM_CTX=32768`, `LLM_TIMEOUT=600`, `MAX_ASYNC_LLM=2`, `MAX_PARALLEL_INSERT=1`, `EMBEDDING_BINDING=ollama`, `EMBEDDING_BINDING_HOST`, `EMBEDDING_MODEL=bge-m3:latest`, `EMBEDDING_DIM=1024`, `OLLAMA_EMBEDDING_NUM_CTX=8192`, the four `LIGHTRAG_*_STORAGE` backends, `ENABLE_LLM_CACHE=false`, the startup auto-scan toggle set **off** to satisfy FR-002a (upstream `env.example` names it `AUTO_SCAN_AT_STARTUP=false` — confirm the exact key against the pinned image in T034), commented `LIGHTRAG_API_KEY` with a prominent "set this before ever moving HOST off 127.0.0.1" warning (FR-028a, research.md Decision 10)
- [X] T006 [P] Create `scripts/lib/common.sh`: `set -euo pipefail` + `IFS=$'\n\t'`; resolve repo root from `${BASH_SOURCE}`; load `.env` (fail with pointer to `.env.example` if absent); preflight that `docker`, `docker compose`, `curl`, `jq`, `tar` exist and the Docker daemon is reachable (exit `3` naming what is missing); `err()`/`die()` helpers that prefix `ERROR:` and print to stderr; uniform exit-code constants (`0/1/2/3/4`); a `wait_for_health` helper that polls `GET http://127.0.0.1:${PORT}/health` until `200` or a configurable timeout (per [contracts/operational-cli.md](./contracts/operational-cli.md) "Shared contract")

**Checkpoint**: `docker compose config` validates; `.env` can be derived from the example; scripts can source `common.sh`.

---

## Phase 3: User Story 1 - Build and query a personal knowledge base (Priority: P1) 🎯 MVP

**Goal**: The owner drops documents in `data/inputs/`, runs one command to ingest them, and asks
grounded, source-referenced questions in the LightRAG Web UI.

**Independent Test**: Copy `tests/fixtures/sample.md` into `data/inputs/`, run `./scripts/start.sh`
then `./scripts/ingest.sh`, open `http://127.0.0.1:9621`, ask the question whose answer only appears
in that fixture, and confirm the answer reflects it and cites the source.

- [X] T007 [US1] Create `scripts/start.sh` (`chmod +x`): source `common.sh`; `docker compose up -d`; `wait_for_health` (default 120s, `.env`-overridable); on success print `http://127.0.0.1:${PORT}` to stdout; on timeout print the tail of `docker compose logs` and "run ./scripts/health.sh" then exit `1`; idempotent when already up (per [contracts/operational-cli.md](./contracts/operational-cli.md) → `start.sh`)
- [X] T008 [P] [US1] Create `scripts/ingest.sh` (`chmod +x`): source `common.sh`; `POST http://127.0.0.1:${PORT}/documents/scan`; poll `GET /documents/track_status/{track_id}` (and the pipeline/document-status listing as fallback) until every document is `processed` or `failed`; print a summary line (N processed / N skipped / N failed); exit `0` if none failed, `1` if any failed; additive only — never deletes KB content (contracts/lightrag-api.md §2, §4, §5)
- [X] T009 [P] [US1] Create `tests/fixtures/sample.md` and `tests/fixtures/sample.pdf` (text-searchable): each contains one distinctive, self-contained fact that appears nowhere else, for use by the smoke test's C4/C5 (research.md Decision 5, Decision 11)
- [X] T010 [US1] Start `docs/OPERATIONS.md`: sections "Start the service", "Add documents & ingest" (`data/inputs/` + `./scripts/ingest.sh`, plus Web UI upload panel — FR-002a), "Query in the Web UI" (source references shown); note no file-watcher / no ingest-on-startup

**Checkpoint**: MVP — ingest a document and get a grounded, sourced answer in the Web UI.

---

## Phase 4: User Story 2 - Knowledge base survives restarts and updates (Priority: P1)

**Goal**: Restarting the service, recreating the container, or replacing the image leaves the
knowledge graph, indexes, and ingestion ledger intact and immediately queryable with no re-ingestion.

**Independent Test**: With a document ingested (US1), run `./scripts/restart.sh --recreate`, then
re-ask the US1 question and confirm the same answer with the doc still `processed` (0 reprocessed).

- [X] T011 [P] [US2] Create `scripts/stop.sh` (`chmod +x`): source `common.sh`; `docker compose down` (containers + network only); MUST NOT accept or pass `-v`/`--volumes`; confirm `data/` is untouched (contracts/operational-cli.md → `stop.sh`, Invariant 1)
- [X] T012 [P] [US2] Create `scripts/restart.sh` (`chmod +x`): default `docker compose restart`; `--recreate` flag → `down` then `up -d` (full container recreation); `wait_for_health` afterward; non-destructive; never `--volumes` (contracts/operational-cli.md → `restart.sh`). Verify the US2 image-replacement case manually with `docker compose pull` + `./scripts/restart.sh --recreate` and confirm 0 documents reprocessed; `update.sh` (T017) automates this path later (FR-012, SC-005)
- [X] T013 [US2] Create `docs/ARCHITECTURE.md` persistence section: the four data categories and where each lives (host bind mounts), how `data/rag_storage/` survives `docker compose down` and image replacement (FR-012, SC-004, SC-005), and Invariant 1 (no routine command writes under `rag_storage/`)
- [X] T014 [US2] Add to `docs/OPERATIONS.md` a "Recover after container loss" runbook: `./scripts/start.sh` recreates the container against the intact bind-mounted KB; full-checkout-loss path (`git clone` → `cp .env.example .env` → restore latest backup → start) — forward-reference `restore.sh` (US3)

**Checkpoint**: Persistence across restart, recreation, and image update is proven and documented.

---

## Phase 5: User Story 3 - Back up and restore the knowledge base (Priority: P2)

**Goal**: One documented command produces a complete, verified backup; one guarded command restores
it without ever silently overwriting a live KB.

**Independent Test**: Ingest documents, `./scripts/backup.sh`, move `data/rag_storage/` aside,
`./scripts/restore.sh <archive>`, re-run the US1 query and confirm identical results.

- [X] T015 [US3] Create `scripts/backup.sh` (`chmod +x`): record running state → `docker compose stop` → `tar -czf data/backups/kb-<UTC-YYYYMMDDTHHMMSSZ>.tar.gz data/rag_storage/` (+ `data/inputs/` with `--with-inputs`) → write `kb-<ts>.manifest` (image tag+digest, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, LightRAG version from `/health`, archive SHA-256, flags) → verify (`tar -tzf` lists `data/rag_storage/`, checksum matches) → return service to prior state; on any failure delete the partial archive and still restore prior running state; `--output DIR`; prints absolute archive path to stdout (research.md Decision 8, contracts/operational-cli.md → `backup.sh`)
- [X] T016 [P] [US3] Create `scripts/restore.sh` (`chmod +x`): positional `<archive-path>`, `--force`, `--assume-yes`, `--with-inputs`; guard sequence — validate archive; compare manifest `EMBEDDING_MODEL`/`EMBEDDING_DIM` to `.env` and warn+confirm on mismatch; compare the manifest's image tag/digest and LightRAG version to the currently configured ones and warn on skew (older-backup-onto-newer-image edge case, spec Edge Cases) without blocking; if `data/rag_storage/` non-empty → exit `4` without `--force`, else interactive `yes/no` (skippable only with `--assume-yes` on non-interactive stdin); `docker compose stop`; move existing `data/rag_storage/` → `data/rag_storage.pre-restore-<ts>/` (never `rm`); extract; `start` + health-check; offer rollback on failure; print displaced-dir path (FR-014, Principle V, research.md Decision 8)
- [X] T017 [US3] Create `scripts/update.sh` (`chmod +x`): run `scripts/backup.sh` first and abort (`1`) if it fails; `docker compose pull`; `docker compose up -d`; health-check; if unhealthy print rollback steps (reset `.env` tag, `up -d`, `restore.sh` the just-made backup); `--tag <image-tag>` updates `.env` `LIGHTRAG_IMAGE_TAG` only after success (FR-018, contracts/operational-cli.md → `update.sh`) — *depends on T015*
- [X] T018 [US3] Add to `docs/OPERATIONS.md` the backup / restore / update runbook, including "back up before every image-tag change" (FR-018) and how the guard prevents overwriting a live KB

**Checkpoint**: Backup produces a verified artifact; restore is proven and non-destructive; updates back up first.

---

## Phase 6: User Story 4 - Query the knowledge base through the API (Priority: P2)

**Goal**: A local process can `POST /query` over loopback and get a structured, source-referenced
response; the same endpoint is unreachable from any other host.

**Independent Test**: With a document ingested, `curl -s -X POST http://127.0.0.1:9621/query -H 'Content-Type: application/json' -d '{"query":"<fixture question>","mode":"hybrid"}'` returns JSON with the answer and source references; the same request to a non-loopback address of the machine is refused.

- [X] T019 [US4] Add a "Query via the API" section to `docs/OPERATIONS.md`: `curl` example for `POST /query` (body `query`, `mode`, `only_need_context`), the structured response shape (answer + source references), and a pointer to [contracts/lightrag-api.md](./contracts/lightrag-api.md) as the integration seam for future MCP/Claude Code work (Principle X — do not build it now)
- [X] T020 [US4] Document and manually verify loopback-only reachability (FR-028, FR-028a, SC-010): confirm `network_mode: host` + `HOST=127.0.0.1` make the Web UI/API unreachable from a non-loopback interface; record the verification procedure in `docs/ARCHITECTURE.md` (exposure model) and `docs/TROUBLESHOOTING.md`

**Checkpoint**: API query works over localhost and is confirmed unreachable off-host.

---

## Phase 7: User Story 5 - Determine whether the system is healthy (Priority: P2)

**Goal**: One command reports overall health and, on failure, names the failing component and likely
cause — covering LightRAG, Ollama reachability, model presence, both model roles, and storage.

**Independent Test**: Run `./scripts/health.sh` with everything up → `HEALTHY`; stop Ollama, run
again → unhealthy output naming Ollama as the cause.

- [X] T021 [US5] Create `scripts/health.sh` (`chmod +x`): the 7 checks from [contracts/operational-cli.md](./contracts/operational-cli.md) → `health.sh` — (1) `GET /health` 200; (2) `GET ${LLM_BINDING_HOST}/api/tags` 200; (3) `LLM_MODEL` & `EMBEDDING_MODEL` present in tags; (4) minimal `POST /api/generate` succeeds; (5) minimal `POST /api/embed` succeeds and vector length == `EMBEDDING_DIM` (else report mismatch — Invariant 3); (6) create+delete temp file in `data/rag_storage/`; (7) warn if `HOST != 127.0.0.1` (FR-028a); each failure line names component + likely cause; `--json`, `--quiet`; exit `0`/`1`; relies only on unauthenticated `/health` fields (FR-020, FR-021)
- [X] T022 [P] [US5] Create `scripts/logs.sh` (`chmod +x`): `docker compose logs --tail N [-f]`, default `--tail 200` follow; optional service-name arg; read-only
- [X] T023 [US5] Enhance `scripts/ingest.sh` failure reporting (FR-022): for each `failed` document print its id/name, the stage that failed, and the reason; processed documents in the same batch remain valid (do not abort the batch — FR-004) — *edits the file from T008; must not run in parallel with T028*
- [X] T024 [P] [US5] Create `docs/TROUBLESHOOTING.md`: Ollama connectivity (host networking vs the bridge + `OLLAMA_HOST=0.0.0.0` + `extra_hosts` fallback from research.md Decision 2), model-not-installed (`ollama pull`), storage-not-writable, disk-full during ingest/backup, interrupted ingestion (re-run `ingest.sh`), restore-onto-live-KB

**Checkpoint**: Health check verifies dependencies (not just "process up") and pinpoints failures.

---

## Phase 8: User Story 6 - Change the language model without rebuilding the KB (Priority: P3)

**Goal**: Swapping the query LLM is a `.env` edit + restart with no re-ingestion; changing the
embedding model surfaces a clear "re-index required" signal and a documented path.

**Independent Test**: Ingest with `qwen2.5:3b-instruct`, change `LLM_MODEL`, `./scripts/restart.sh`,
re-run the US1 query and confirm the new model answers from the unchanged KB.

- [X] T025 [P] [US6] Create `docs/MODEL_SELECTION.md`: the tested CPU combination and why (research.md Decision 4); change the query LLM = edit `LLM_MODEL` → `restart.sh`, no re-index (FR-023); change the embedding model = edit `EMBEDDING_MODEL` **and** `EMBEDDING_DIM` → re-index; the `EMBEDDING_DIM` must-match-model gotcha (Invariant 3); `OLLAMA_LLM_NUM_CTX` ≥ 32768 rationale
- [X] T026 [US6] Add embedding model/dimension mismatch detection: `scripts/health.sh` and `scripts/restore.sh` compare current `.env` `EMBEDDING_MODEL`/`EMBEDDING_DIM` against the KB/backup manifest and surface "re-index required — stale results must not be presented as current" (FR-025) — *edits files from T021 and T016*
- [X] T027 [US6] Add a "Re-index after an embedding-model change" procedure to `docs/OPERATIONS.md`: re-run `ingest.sh` against the existing `data/inputs/` (sources reused, not re-collected); note the old vector index is superseded

**Checkpoint**: Query-LLM swap needs no re-ingestion; embedding-model change is guided and safe.

---

## Phase 9: User Story 7 - Skip documents that have not changed (Priority: P3)

**Goal**: Re-running ingestion reprocesses only new or modified documents; an unchanged collection
reprocesses zero.

**Independent Test**: Ingest a set, re-run `./scripts/ingest.sh` with no changes → 0 reprocessed;
modify one file → only that one reprocessed.

- [X] T028 [US7] Refine `scripts/ingest.sh` summary output to distinguish `processed` / `skipped (unchanged)` / `skipped (unsupported)` / `failed` counts, reading LightRAG's ingestion ledger (FR-005, FR-006, SC-006) — *edits the file from T008/T023; sequential with T023*
- [X] T029 [US7] Document change-detection behaviour in `docs/ARCHITECTURE.md`: LightRAG dedupes by filename + content hash; unchanged skipped, modified reprocessed, interrupted resumed (FR-007); removing a file from `data/inputs/` does **not** remove its KB content (spec Assumption)

**Checkpoint**: Incremental ingestion confirmed; behaviour documented.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: The end-to-end validation artifact, final documentation, image pinning, and on-machine
verification.

- [X] T030 Create `scripts/smoke-test.sh` (`chmod +x`): run contract tests **C1–C8** from [contracts/lightrag-api.md](./contracts/lightrag-api.md) in order using `tests/fixtures/`, stopping at the first failure; `--keep` to leave state in place; by default C8 backs up before wiping and restores afterward, and pre-test `data/inputs/` + `data/rag_storage/` contents are restored on exit; print a per-check PASS/FAIL table (FR-031, SC-011, SC-012) — *depends on T007–T028*
- [X] T031 Finalise `docs/ARCHITECTURE.md`: component overview, Docker↔Ollama interaction under host networking, data-location map, the "several hundred documents" scale ceiling and the concrete signal that would justify a graph/vector DB (research.md Decision 6, SC-013)
- [X] T032 [P] Finalise `README.md`: 30-minute quickstart summary, the command table (start/stop/restart/health/logs/ingest/backup/restore/update/smoke-test), links to every `docs/*.md` and to `specs/001-lightrag-knowledge-base/quickstart.md`
- [X] T033 Pin `LIGHTRAG_IMAGE_TAG` to an explicit non-floating tag in `.env.example` and record the resolved image digest in `docs/ARCHITECTURE.md` (research.md open items) — *edits files from T005 and T031; sequential with T031*
- [X] T034 On the target EndeavourOS machine: follow `specs/001-lightrag-knowledge-base/quickstart.md` first-time setup, then run `./scripts/smoke-test.sh` and confirm all of C1–C8 pass; reconcile the research.md "open items" (`LIGHTRAG_PARSER` default for `.txt`, `/health` unauthenticated fields, `bge-m3` actual dimension, document-status endpoint path, the startup auto-scan key name and that it is honoured) against the pinned image and adjust `.env.example` / scripts if they differ
- [X] T035 [P] (Optional) Add `bats` checks under `tests/unit/` for `scripts/lib/common.sh` — argument parsing, exit-code constants, `.env` loading, missing-dependency detection

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: after Setup. **Blocks every user story.**
- **US1 (Phase 3)**: after Foundational. No dependency on other stories — this is the MVP.
- **US2 (Phase 4)**: after Foundational. Independently testable; T014 forward-references `restore.sh`.
- **US3 (Phase 5)**: after Foundational. Independently testable. `update.sh` (T017) depends on `backup.sh` (T015).
- **US4 (Phase 6)**: after Foundational; easiest to verify with a document already ingested (US1).
- **US5 (Phase 7)**: after Foundational. T023 edits `scripts/ingest.sh` (from US1 T008).
- **US6 (Phase 8)**: after Foundational. T026 edits `health.sh` (T021) and `restore.sh` (T016).
- **US7 (Phase 9)**: after Foundational. T028 edits `scripts/ingest.sh` — **sequential with T023**.
- **Polish (Phase 10)**: `smoke-test.sh` (T030) needs all scripts; T034 needs T030.

### Cross-story file-conflict constraints (do NOT parallelize)

- `scripts/ingest.sh`: T008 → T023 → T028 (same file, three phases).
- `scripts/health.sh`: T021 → T026.
- `scripts/restore.sh`: T016 → T026.
- `docs/ARCHITECTURE.md`: T013 → T020 → T029 → T031 → T033.
- `docs/OPERATIONS.md`: T010 → T014 → T018 → T019 → T027.
- `.env.example`: T005 → T033.

### Story completion order (recommended)

Foundational → **US1 (MVP)** → US2 → US3 → US4 → US5 → US6 → US7 → Polish.
US2–US7 can be reordered freely after Foundational; keep US3 before the Polish `smoke-test` (C8 needs backup/restore).

---

## Parallel Execution Examples

**Phase 1 (Setup)** — after T001:

```bash
Task: "T002 Create .gitignore at repo root"
Task: "T003 Create README.md skeleton at repo root"
```

**Phase 2 (Foundational)** — all three are different files:

```bash
Task: "T004 Create compose.yaml"
Task: "T005 Create .env.example"
Task: "T006 Create scripts/lib/common.sh"
```

**Phase 3 (US1)**:

```bash
Task: "T008 Create scripts/ingest.sh"
Task: "T009 Create tests/fixtures/sample.md and sample.pdf"
# T007 (start.sh) can also go here; T010 waits for T007+T008
```

**Phase 5 (US3)**:

```bash
Task: "T015 Create scripts/backup.sh"
Task: "T016 Create scripts/restore.sh"
Task: "T018 Add backup/restore/update runbook to docs/OPERATIONS.md"
# T017 (update.sh) waits for T015
```

**Docs sprint** (once the scripts they describe exist), each a different file:

```bash
Task: "T024 Create docs/TROUBLESHOOTING.md"
Task: "T025 Create docs/MODEL_SELECTION.md"
# T031 (docs/ARCHITECTURE.md) is NOT parallel-safe — it shares the file with T013/T020/T029
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Phase 1: Setup (T001–T003).
2. Phase 2: Foundational (T004–T006).
3. Phase 3: US1 (T007–T010).
4. **STOP and validate**: ingest `tests/fixtures/sample.md`, query it in the Web UI, confirm a grounded, sourced answer.

That is a usable personal knowledge base.

### Incremental delivery

Add US2 (persistence proof) → US3 (backup/restore) → US4 (API) → US5 (health) → US6 (model swap) →
US7 (incremental ingest). Each phase ends at a checkpoint that is independently testable and adds
value without breaking earlier stories. Finish with Phase 10: `smoke-test.sh` turns SC-011/SC-012
into an executable check on the target machine.

---

## Notes

- `[P]` = different file, no dependency on an incomplete task.
- Every script sources `scripts/lib/common.sh`; none hard-codes a model, port, path, image tag, or credential (Principle IV, VI; FR-024, FR-030).
- No routine script passes `-v`/`--volumes` to `docker compose down` (Invariant 1, FR-013).
- Only `restore.sh` may displace KB state, and only behind its guard + confirmation (FR-014, Principle V).
- `data/backups/` is never mounted into the container (Invariant 4).
- Commit after each task or logical group.
