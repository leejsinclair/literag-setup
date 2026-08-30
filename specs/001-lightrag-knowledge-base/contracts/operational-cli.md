# Contract: Operational CLI (scripts/)

**Date**: 2026-08-30
**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md)

Every operation the spec requires (FR-019) has exactly one script under `scripts/`. This
document is the behavioural contract each script must satisfy: invocation, configuration
source, exit codes, side effects, and the data-safety guarantee. Implementation lives in
`/speckit-tasks` output, not here.

## Shared contract (all scripts)

- **Shebang / mode**: `#!/usr/bin/env bash`, `set -euo pipefail`, `IFS=$'\n\t'`.
- **Config source**: `scripts/lib/common.sh` loads `.env` from the repo root. Scripts
  MUST NOT hard-code model names, ports, paths, image tags, or credentials (Principle IV,
  VI; FR-024, FR-030). Any value they need comes from `.env` or `docker compose` output.
- **Working directory independence**: a script run from any CWD resolves the repo root from
  its own location.
- **Docker Compose only**: container lifecycle actions call `docker compose ...` against
  the repo `compose.yaml`. No direct `docker run`/`docker rm`/`docker stop <id>`
  (Operational model constraint).
- **Preflight** (via `common.sh`): verify `docker`, `docker compose`, `curl`, `jq`, `tar`
  are present; verify `.env` exists (else point to `.env.example`); for container ops
  verify the Docker daemon is reachable. Missing prerequisite ⇒ exit `3` with a message
  naming what is missing.
- **Exit codes** (uniform):
  | Code | Meaning |
  |------|---------|
  | `0` | success |
  | `1` | operation failed (action attempted, did not succeed) |
  | `2` | usage error (bad/missing arguments) |
  | `3` | unmet prerequisite (missing tool, missing `.env`, daemon down) |
  | `4` | refused for safety (would overwrite/destroy data without explicit override) |
- **Output discipline**: human-readable progress to stderr; machine-relevant result (e.g.
  backup path) to stdout. Errors are prefixed `ERROR:` and state the failing component and
  the likely cause (FR-021, Principle VII) — never a bare stack trace or `set -x` dump.
- **Data safety**: no script deletes or overwrites anything under `data/rag_storage/` or
  `data/backups/` except `restore.sh` under its guard. `stop.sh`/`restart.sh`/`update.sh`
  never pass `-v`/`--volumes` to `docker compose down` (Invariant 1, FR-013).

---

## `start.sh`

- **Args**: none.
- **Action**: `docker compose up -d`; then poll `GET /health` until `200` or a readiness
  timeout (default 120 s, overridable via `.env`).
- **Success (`0`)**: `/health` returns `200`; prints the Web UI URL
  (`http://127.0.0.1:${PORT}`).
- **Failure (`1`)**: timeout waiting for health ⇒ print last `docker compose logs` tail and
  a hint to run `health.sh`.
- **Safety**: non-destructive. Idempotent (re-running when already up is a no-op + health
  check).

## `stop.sh`

- **Args**: none.
- **Action**: `docker compose down` (containers + network only; **bind mounts retained**).
- **Success (`0`)**: container stopped and removed; `data/` untouched.
- **Safety**: non-destructive. MUST NOT accept or pass a `--volumes` flag.

## `restart.sh`

- **Args**: optional `--recreate` (full `down` then `up -d`, i.e. container recreation) vs
  default `docker compose restart`.
- **Action**: restart the service; then the same health poll as `start.sh`.
- **Success (`0`)**: `/health` `200` after restart.
- **Safety**: non-destructive. Used by `smoke-test.sh` with `--recreate` to prove KB
  survives container recreation (FR-012, SC-005).

## `health.sh`

- **Args**: optional `--json` (emit a structured result), optional `--quiet`.
- **Checks** (each reported individually; overall status = worst):
  1. **LightRAG**: `GET /health` → `200`.
  2. **Ollama reachable**: `GET ${LLM_BINDING_HOST}/api/tags` → `200`.
  3. **Models present**: `LLM_MODEL` and `EMBEDDING_MODEL` appear in `/api/tags`.
  4. **LLM role**: minimal `POST /api/generate` (or `/api/chat`) succeeds.
  5. **Embedding role**: minimal `POST /api/embed` succeeds; response vector length ==
     `EMBEDDING_DIM` (else report the mismatch explicitly — Invariant 3).
  6. **Storage writable**: create+delete a temp file in `data/rag_storage/` (host-side).
  7. **Config sanity**: `HOST` == `127.0.0.1` (warn if not — exposure risk, FR-028a).
- **Exit**: `0` if all pass; `1` if any check fails. Each failure line names the component
  and the likely cause (FR-020, FR-021; User Story 5).
- **Auth note**: relies only on unauthenticated `/health` fields (contract
  `lightrag-api.md` §1).
- **Safety**: read-only (the storage check cleans up its own temp file).

## `logs.sh`

- **Args**: optional `-f`/`--follow` (default follow), optional `--tail N` (default 200),
  optional service name.
- **Action**: `docker compose logs --tail N [-f]`.
- **Safety**: read-only.

## `ingest.sh`

- **Args**: none (operates on whatever is in `data/inputs/`). Optional `--wait-timeout S`.
- **Action**: `POST /documents/scan`; then poll track status / document status until all
  documents are terminal (`processed`/`failed`).
- **Success (`0`)**: every document `processed`. Prints a summary: N processed, N skipped
  (unchanged/unsupported), 0 failed.
- **Partial (`1`)**: ≥1 document `failed` — print each failed document, its stage, and its
  reason (FR-022); processed documents remain valid (batch not aborted — FR-004).
- **Safety**: additive only. Never removes KB content. Safe to re-run (unchanged docs
  skipped — FR-005, SC-006).

## `backup.sh`

- **Args**: optional `--with-inputs`, optional `--output DIR` (default `data/backups/`).
- **Action** (research Decision 8):
  1. Record whether the service was running.
  2. `docker compose stop` (quiesce for a consistent snapshot).
  3. `tar -czf data/backups/kb-<UTC-timestamp>.tar.gz data/rag_storage/`
     (+ `data/inputs/` if `--with-inputs`).
  4. Write `kb-<timestamp>.manifest` (image tag+digest, `EMBEDDING_MODEL`,
     `EMBEDDING_DIM`, LightRAG version, archive SHA-256, flags).
  5. Verify: `tar -tzf` lists `data/rag_storage/`; checksum matches manifest.
  6. If the service was running, `docker compose start`; health-check.
- **Success (`0`)**: prints the absolute archive path to stdout; archive verified.
- **Failure (`1`)**: any step fails ⇒ archive is removed (no partial artifact left
  claiming success); service is returned to its prior running state regardless.
- **Safety**: does not modify or delete `data/rag_storage/`. Does not delete older backups.

## `restore.sh`

- **Args**: `<archive-path>` (required, positional). Optional `--force`. Optional
  `--with-inputs` (restore inputs too if present in the archive).
- **Guard sequence** (FR-014, Principle V, Edge Case "restore onto a live KB"):
  1. Validate the archive: exists, `tar -tzf` succeeds, contains `data/rag_storage/`. Fail
     `2`/`1` otherwise.
  2. If a sidecar manifest exists, compare `EMBEDDING_MODEL`/`EMBEDDING_DIM` to current
     `.env`; on mismatch print a prominent warning (index/model mismatch ⇒ re-index may be
     needed — FR-025) and require confirmation even with `--force`.
  3. If `data/rag_storage/` is non-empty:
     - without `--force` ⇒ exit `4` ("refusing to overwrite an existing knowledge base;
       re-run with --force to move it aside and restore").
     - with `--force` ⇒ interactive `yes/no` confirmation (unless stdin is non-interactive
       AND an explicit `--assume-yes` is also given).
  4. `docker compose stop`.
  5. Move existing `data/rag_storage/` → `data/rag_storage.pre-restore-<timestamp>/`
     (never `rm`).
  6. Extract the archive into place.
  7. `docker compose start`; health-check; run one query if a known probe is configured.
- **Success (`0`)**: service healthy on the restored KB; prints the path of the displaced
  pre-restore directory (if any).
- **Failure (`1`)**: on extract/health failure, offer to roll back by swapping the
  pre-restore directory back.
- **Safety**: never deletes prior KB state; only displaces it. Confirmation required for
  any overwrite.

## `update.sh`

- **Args**: optional `--tag <image-tag>` (otherwise uses current `.env`
  `LIGHTRAG_IMAGE_TAG`; if `--tag` given, updates `.env` after a successful update).
- **Action** (FR-018):
  1. Run `backup.sh` — abort the update (`1`) if the backup fails.
  2. `docker compose pull`.
  3. `docker compose up -d`.
  4. Health-check; if unhealthy, print the rollback instruction (set `.env` tag back, `up
     -d`, `restore.sh` the just-made backup).
- **Success (`0`)**: new image running and healthy; KB intact; prints backup path used.
- **Safety**: always backs up first; never removes the prior image automatically.

## `smoke-test.sh`

- **Args**: optional `--keep` (do not tear down / restore pre-test state at the end).
- **Action**: executes contract tests C1–C8 from `lightrag-api.md` in order, using
  fixtures in `tests/fixtures/`, stopping at the first failure.
- **Success (`0`)**: all checks pass; prints a per-check PASS table (satisfies SC-011,
  SC-012).
- **Failure (`1`)**: prints which check failed and the diagnostic; leaves the system in a
  state the owner can inspect.
- **Safety**: uses a dedicated fixture doc set; C8 backs up before wiping and restores
  afterwards; by default returns `data/inputs/` and `data/rag_storage/` to their
  pre-test contents on exit.

---

## Traceability

| Spec requirement | Script(s) |
|------------------|-----------|
| FR-002 / FR-002a ingestion | `ingest.sh` (+ Web UI upload, unchanged) |
| FR-005 / FR-006 skip unchanged / reprocess changed | `ingest.sh` (relies on LightRAG ledger) |
| FR-007 recover interrupted ingestion | `ingest.sh` re-run (LightRAG resumes) |
| FR-012 persistence across recreation/update | `stop.sh`, `restart.sh --recreate`, `update.sh` (bind mounts) |
| FR-013 no destructive side effects | shared contract; `stop.sh`/`restart.sh` never `--volumes` |
| FR-014 destructive ops need confirmation | `restore.sh` guard; `/documents/clear` excluded |
| FR-015 / FR-016 / FR-017 backup & restore complete | `backup.sh`, `restore.sh` |
| FR-018 backup before upgrade | `update.sh` step 1 |
| FR-019 one command per operation | this file (all scripts) |
| FR-020 / FR-021 dependency-verifying health + actionable failures | `health.sh` |
| FR-022 per-document / per-stage failure reporting | `ingest.sh` |
| FR-031 end-to-end validation | `smoke-test.sh` |
| SC-001 up + healthy in < 30 min from clean checkout | `start.sh` + docs |
| SC-006 unchanged collection reprocesses 0 | `ingest.sh` |
| SC-007 restore returns identical results | `smoke-test.sh` C8 |
| SC-010 loopback-only reachability | `health.sh` config check; `smoke-test.sh` C7 |
| SC-011 / SC-012 validation on CPU | `smoke-test.sh` |
