# Contract: LightRAG REST API (consumed + exposed)

**Date**: 2026-08-30
**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md)

This feature does not implement an API. It **exposes** the LightRAG server's REST API
unchanged as the integration boundary (Principle X, FR-009) and **consumes** a small
subset from the operational scripts. This document fixes the subset the deployment depends
on, so a future upstream change that breaks one of these is caught deliberately (against
the pinned image version).

- **Base URL**: `http://127.0.0.1:${PORT}` (default `http://127.0.0.1:9621`) — loopback
  only. Unreachable from any other host (FR-028, SC-010, User Story 4 scenario 3).
- **Authentication**: none in the initial implementation (clarification Q4, FR-028a).
  No `X-API-Key` header is sent. If `LIGHTRAG_API_KEY` is ever set, every script below must
  send `X-API-Key: <key>` and this contract is revised.
- **Auth-dependent fields**: `GET /health` returns richer diagnostics only to
  authenticated callers. Scripts MUST rely only on the always-present fields (status +
  basic config echo).
- **Web UI**: served at `/webui/`. `GET /` returns `307` → `/webui/` (verified against
  v1.5.6); the smoke test follows the redirect and asserts `200` + HTML.
- **Interactive docs**: `GET /docs` (Swagger UI), `GET /redoc` — used by the smoke test to
  assert "API reachable".
- Exact request/response schemas are the pinned image version's `GET /openapi.json`. The
  shapes below are the contract *we* depend on; treat anything else as opaque.

---

## Endpoints consumed by scripts

### 1. `GET /health` — service + dependency status

- **Used by**: `health.sh`, `start.sh` (readiness wait), `smoke-test.sh`, `update.sh`,
  `backup.sh`/`restore.sh` (post-op verification).
- **Success**: `200` with a JSON body containing at least an overall status field and an
  echo of the configured LLM/embedding bindings.
- **Contract expectations**:
  - `200` ⇒ the LightRAG process is up and serving.
  - The script does **not** treat `/health` alone as sufficient — it additionally probes
    Ollama and storage (see `operational-cli.md` → `health.sh`) because `/health` may not
    fully verify dependencies for an unauthenticated caller (FR-020).
- **Failure handling**: connection refused ⇒ "LightRAG not running / not ready"; non-200 ⇒
  surface status code and body.

### 2. `POST /documents/scan` — trigger ingestion of the input directory

- **Used by**: `ingest.sh`, `smoke-test.sh`.
- **Semantics**: LightRAG scans `INPUT_DIR`, deduplicates by filename + content hash, and
  enqueues new/changed files. Already-recorded documents are skipped **whatever their
  state** (FR-005). Additive only — never deletes KB content (Invariant 1).
- **Success**: `2xx`; response may include a track id / accepted count.
- **After calling**: poll for completion via endpoint 4 and/or 5.
- **Idempotency**: safe to call repeatedly; a no-op scan reprocesses 0 documents (SC-006).

### 3. `POST /documents/upload` — ad-hoc single-file ingestion

- **Used by**: not required by any script; available via Web UI and for future callers
  (FR-002a). Documented in `contracts` for completeness of the exposed surface.
- **Request**: `multipart/form-data` with a file part.
- **Success**: `2xx` with a track id.

### 4. `GET /documents/track_status/{track_id}` — per-submission progress

- **Used by**: `ingest.sh`, `smoke-test.sh` when a scan/upload returned a track id.
- **Contract**: poll until every document under the track id reaches a terminal state
  (`processed` or `failed`). A `failed` document MUST expose a human-readable reason and
  which stage failed (FR-022).

### 5. Pipeline / document status listing — completion + "no re-processing" check

- **Used by**: `ingest.sh` (fallback when no track id), `smoke-test.sh` (assert that after
  a restart the fixture docs are still `processed` and were not reprocessed — FR-007,
  SC-004/SC-005).
- **Endpoint**: the pinned version's documents-status/pipeline-status route (confirm exact
  path from `/openapi.json`; commonly `GET /documents` or `GET /documents/pipeline_status`).
- **Contract**: returns per-document status and counts; a document present with status
  `processed` and unchanged hash is proof it was not reprocessed.

### 6. `POST /query` — retrieval query (JSON)

- **Used by**: `smoke-test.sh`; the reference example for future integrations.
- **Request body** (fields this feature relies on):

  ```json
  {
    "query": "string (required)",
    "mode": "hybrid",            // one of: naive | local | global | hybrid | mix
    "only_need_context": false
  }
  ```

- **Success**: `200` with a JSON body containing the answer text and references to the
  contributing source material (FR-010).
- **Contract expectations**:
  - An answerable question returns the fact from the ingested fixture **and** at least one
    source reference (User Story 1 scenario 2, User Story 4 scenario 1).
  - A question with no supporting content returns a response that makes the absence clear
    and does not fabricate document-sourced content (FR-011, User Story 1 scenario 3).

### 7. `POST /query/stream` — streaming variant

- **Used by**: none of the scripts; part of the exposed surface for future callers.

### 8. `POST /documents/clear` — destructive, NOT used by any script

- **Explicitly excluded** from the routine command set. If ever invoked it is a manual,
  owner-initiated action requiring its own confirmation (FR-014, Principle V). Listed here
  so implementers know it exists and must not wire it into `stop.sh`/`restore.sh`/etc.

---

## Endpoints exposed but not consumed (integration seam — Principle X)

`POST /api/chat`, `POST /api/generate` (Ollama-compatible), `POST /documents/text`,
`POST /documents/texts`, `POST /documents/reprocess_failed`, `DELETE /documents/{doc_id}`,
`GET /graph*` routes, `GET /docs`, `GET /redoc`, `GET /openapi.json`.

These remain available for a future Claude Code / MCP integration. This feature does not
build, stub, or wrap them.

---

## Contract tests (executed by `smoke-test.sh`)

| # | Assertion | Spec ref |
|---|-----------|----------|
| C1 | `GET /health` → 200 within readiness timeout after `start.sh` | FR-019, SC-001 |
| C2 | `GET /` → 200 + HTML after following the 307 redirect to `/webui/` (Web UI reachable) | FR-008, SC-011 |
| C3 | `GET /docs` → 200 (API reachable) | FR-009, SC-011 |
| C4 | `POST /documents/scan` after placing `.md` + `.pdf` fixtures → both reach `processed` | FR-002, FR-003, Decision 5 |
| C5 | `POST /query` with a fixture-only question → answer contains expected fact + ≥1 source ref | FR-010, FR-011, SC-003 |
| C6 | After `restart.sh`: same query → same fact; fixture docs still `processed`, reprocessed count = 0 | FR-007, FR-012, SC-004, SC-005, SC-006 |
| C7 | `POST /query` from a non-loopback address (if a second interface exists) → connection refused / unreachable | FR-028, SC-010, User Story 4 scenario 3 |
| C8 | After `backup.sh` + wipe + `restore.sh`: same query → same fact | FR-015, FR-016, SC-007 |

Exact endpoint paths for C4/C6 status polling are pinned from the running version's
`/openapi.json` during implementation.
