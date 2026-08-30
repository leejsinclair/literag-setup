# Phase 1 Data Model: LightRAG Local Knowledge Base

**Date**: 2026-08-30
**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

This feature builds no database schema of its own. The "data model" is the mapping between
the spec's Key Entities and the concrete artifacts LightRAG persists on disk, plus the one
artifact this feature adds (the backup archive) and the configuration surface. Field-level
internals of LightRAG's JSON files are owned by LightRAG and are treated as opaque except
where an operation depends on them.

Legend: **Host path** is relative to the repo root; **container path** is where LightRAG
sees it.

---

## Entity: Source Document

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Source Document; FR-001, FR-002, FR-004, FR-005, FR-006 |
| Storage | `data/inputs/` (host) ↔ `/app/data/inputs` (container, `INPUT_DIR`) |
| Identity | File path/name within the input directory |
| Attributes | path, byte content, extension/type, filesystem mtime, content hash (MD5, computed by LightRAG), ingestion status (mirrored in Document Metadata) |
| Supported types at launch | `.txt`, `.md`, text-searchable `.pdf` (research Decision 5). Other extensions → skipped + reported (FR-004) |
| Lifecycle | `placed in inputs/` → (scan) → `queued` → `parsing` → `analyzing`/`processing` → `processed` \| `failed`. Removal from `inputs/` after ingestion does **not** remove KB content (spec Assumption; documented in `OPERATIONS.md`) |
| Change detection | Unchanged (same name + same content hash) ⇒ skipped on re-scan (FR-005). New or modified ⇒ processed (FR-006). Interrupted mid-pipeline ⇒ resumed on next scan without re-extraction (FR-007) |
| Validation rules | Unsupported extension → skip, do not abort batch. Scanned/image-only PDF (no extractable text) → expected to `fail` with a per-document reason (Edge Cases; no OCR in scope) |
| Backed up | Optional (`backup.sh --with-inputs`). Not required for KB recovery — the KB is self-contained once processed |

---

## Entity: Knowledge Graph

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Knowledge Graph; FR-003 |
| Backend | `NetworkXStorage` (`LIGHTRAG_GRAPH_STORAGE`) |
| Storage | `data/rag_storage/graph_chunk_entity_relation.graphml` (+ related files) |
| Content | Entities and relationships extracted from document chunks by the LLM |
| Persistence | Survives restart, container recreation, image update (bind mount) — FR-012, SC-004, SC-005 |
| Rebuild trigger | Re-ingestion only. Not affected by changing the **query** LLM (FR-023) |
| Load characteristic | Loaded into memory at startup; very large graphs are the documented signal to reconsider a graph DB (research Decision 6, SC-013) |

---

## Entity: Retrieval Index

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Retrieval Index; FR-003, FR-025 |
| Backend | `NanoVectorDBStorage` (`LIGHTRAG_VECTOR_STORAGE`) |
| Storage | `data/rag_storage/vdb_*.json` (entities, relationships, chunks) |
| Derived from | Document chunk text + the configured embedding model |
| Model lock | Vector dimensionality = `EMBEDDING_DIM`, fixed to `EMBEDDING_MODEL`. Changing the embedding model invalidates this index (FR-025, User Story 6 scenario 2) |
| Invalidation handling | On embedding-model change the system must signal that re-indexing is required and must not present stale results as current. Operationally: `restore.sh`/`health.sh` compare current `.env` embedding settings against the KB manifest and warn on mismatch; `MODEL_SELECTION.md` documents the re-index procedure (re-scan sources; original documents reused, not re-collected) |
| Persistence | Bind mount — survives restart/recreation/update |

---

## Entity: Document Metadata / Ingestion Ledger

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Document Metadata / Ingestion Ledger; FR-005, FR-007, FR-022 |
| Backend | `JsonDocStatusStorage` (`LIGHTRAG_DOC_STATUS_STORAGE`) + `JsonKVStorage` (`LIGHTRAG_KV_STORAGE`) |
| Storage | `data/rag_storage/` — doc-status records and `kv_store_*.json` |
| Per-document fields (LightRAG-owned, consumed read-only by scripts) | id/name, content hash, status (`pending`/`processing`/`processed`/`failed`), timestamps, chunk count, error/reason, optional `metadata.llm_truncation` |
| Used by | `ingest.sh` (poll until `processed`/`failed`), `health.sh` / `smoke-test.sh` (assert no re-processing after restart), failure reporting (FR-022: identify which document and which stage failed) |
| Persistence | Bind mount — this ledger is what makes "skip unchanged" and "resume after interruption" work across restarts |

---

## Entity: Query

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Query; FR-008, FR-009, FR-010, FR-011 |
| Nature | Transient request/response — not persisted by this feature (LightRAG may cache LLM responses if `ENABLE_LLM_CACHE=true`; default here keeps caching off for determinism during validation) |
| Request attributes | question text, retrieval mode/parameters (LightRAG `mode`: naive/local/global/hybrid/mix), response format |
| Response attributes | answer text, source references / contributing chunks (FR-010), explicit "no supporting information found" signal distinct from a substantive answer (FR-011) |
| Interfaces | Web UI (FR-008) and `POST /query` + `POST /query/stream` (FR-009); see [contracts/lightrag-api.md](./contracts/lightrag-api.md) |

---

## Entity: Configuration

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Configuration; FR-024, FR-030; Principles IV & VI |
| Storage | `.env` (host, gitignored, `chmod 0644`) ↔ `/app/.env` (container, read by LightRAG). `.env.example` committed |
| Never in VCS | `.env`, any provider API key, host-specific paths |
| Key groups | see table below |
| Change semantics | Query-LLM change → restart, no re-index (FR-023). Embedding-model change → re-index required (FR-025). Binding/port change → restart; moving `HOST` off `127.0.0.1` is gated by "add auth first" (FR-028a) |

### Configuration keys (authoritative list for `.env.example`)

| Key | Default in `.env.example` | Purpose | Changing it |
|-----|---------------------------|---------|-------------|
| `LIGHTRAG_IMAGE_TAG` | pinned tag (record digest in `ARCHITECTURE.md`) | which official image runs | `update.sh` only |
| `HOST` | `127.0.0.1` | app bind address (loopback) | ⚠ off-loopback requires auth first (FR-028a) |
| `PORT` | `9621` | app port | restart |
| `WORKING_DIR` | `/app/data/rag_storage` | LightRAG persistent state | do not change |
| `INPUT_DIR` | `/app/data/inputs` | source document dir | do not change |
| `PROMPT_DIR` | `/app/data/prompts` | prompt overrides | optional |
| `LLM_BINDING` | `ollama` | LLM provider | Principle IV |
| `LLM_BINDING_HOST` | `http://localhost:11434` | host Ollama endpoint | matches networking mode |
| `LLM_MODEL` | `qwen2.5:3b-instruct` | query + extraction model | restart, no re-index (FR-023) |
| `OLLAMA_LLM_NUM_CTX` | `32768` | context window for extraction | keep ≥ 32768 |
| `LLM_TIMEOUT` | `600` | per-request timeout (CPU-slow) | raise if timeouts seen |
| `MAX_ASYNC_LLM` | `2` | concurrent LLM calls | lower to reduce CPU load |
| `MAX_PARALLEL_INSERT` | `1` | docs processed concurrently | keep low on CPU |
| `EMBEDDING_BINDING` | `ollama` | embedding provider | Principle IV |
| `EMBEDDING_BINDING_HOST` | `http://localhost:11434` | host Ollama endpoint | matches networking mode |
| `EMBEDDING_MODEL` | `bge-m3:latest` | embedding model | ⚠ re-index required (FR-025) |
| `EMBEDDING_DIM` | `1024` | vector dimension | MUST match `EMBEDDING_MODEL` |
| `OLLAMA_EMBEDDING_NUM_CTX` | `8192` | embedding context | model-dependent |
| `LIGHTRAG_KV_STORAGE` | `JsonKVStorage` | file-based KV | changing = new feature (Principle IX) |
| `LIGHTRAG_DOC_STATUS_STORAGE` | `JsonDocStatusStorage` | file-based doc status | ″ |
| `LIGHTRAG_GRAPH_STORAGE` | `NetworkXStorage` | file-based graph | ″ |
| `LIGHTRAG_VECTOR_STORAGE` | `NanoVectorDBStorage` | file-based vectors | ″ |
| `ENABLE_LLM_CACHE` | `false` | deterministic validation | may enable later |
| `LIGHTRAG_API_KEY` | *(unset, commented)* | API auth | ⚠ set before any network exposure (FR-028a) |
| `WEBUI_TITLE` / `WEBUI_DESCRIPTION` | cosmetic | Web UI labels | optional |

Exact key names/defaults are reconciled against the **pinned image version's**
`env.example` during implementation (research.md open items).

---

## Entity: Backup Artifact

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Backup Artifact; FR-015, FR-016, FR-017, FR-018; User Story 3 |
| Produced by | `scripts/backup.sh` (and automatically by `update.sh`) |
| Location | `data/backups/` (host only; not mounted into the container) |
| Naming | `kb-<UTC-YYYYMMDDTHHMMSSZ>.tar.gz` + sidecar `kb-<...>.manifest` |
| Contents (archive) | full `data/rag_storage/` tree (graph, vectors, KV, doc status) — everything needed for a complete restore (FR-017). Optionally `data/inputs/` with `--with-inputs` |
| Contents (manifest) | image tag + digest, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, LightRAG version (from `/health`), archive SHA-256, timestamp, `--with-inputs` flag |
| Excluded by design | Docker images, Python environment, Ollama model weights (spec Backup constraint; all reconstructible) |
| Consistency | Service stopped/quiesced during archive creation to avoid torn JSON writes |
| Restore rules | `restore.sh` refuses a non-empty `data/rag_storage/` without `--force` **and** interactive confirmation; on `--force` the existing state is moved to `data/rag_storage.pre-restore-<timestamp>/`, never deleted (FR-014, Principle V, Edge Case "restore onto a live KB"); warns if manifest embedding settings ≠ current `.env` |
| Retention | Manual (owner deletes old archives). No automated rotation in scope |

---

## Relationships & invariants

```text
Source Document (data/inputs/*)
      │  scan / ingest  (additive; unchanged docs skipped)
      ▼
Ingestion Ledger (doc_status)  ──drives──►  "skip unchanged", "resume after interruption",
      │                                       per-document failure reporting
      ├──────────────► Knowledge Graph (graphml)      ── query ──►  Query response
      └──────────────► Retrieval Index (vdb_*.json)   ──         │   + source references
                                   ▲                             │
              EMBEDDING_MODEL / EMBEDDING_DIM  ── lock ───────────┘
                                   │
                        change ⇒ Retrieval Index invalid ⇒ re-index required (FR-025)

All of {Knowledge Graph, Retrieval Index, Ingestion Ledger, KV store}
  live under data/rag_storage/  ─►  survive `docker compose down` + image replace (FR-012)
                                └─►  captured whole by Backup Artifact (FR-017)

Query LLM (LLM_MODEL)  ── independent of stored KB ──►  change ⇒ restart only, no re-index (FR-023)
```

**Invariant 1** — No routine command (`start`, `stop`, `restart`, `health`, `logs`,
`ingest`, `query`) deletes or overwrites anything under `data/rag_storage/` (FR-013).

**Invariant 2** — Only `restore.sh` (guarded) and an explicit LightRAG "clear documents"
action (owner-initiated, out of the routine set) can remove KB content (FR-014).

**Invariant 3** — `EMBEDDING_DIM` in `.env` always equals the native dimension of
`EMBEDDING_MODEL`; a mismatch is a hard configuration error surfaced by `health.sh`.

**Invariant 4** — The container never has a path to `data/backups/`.
