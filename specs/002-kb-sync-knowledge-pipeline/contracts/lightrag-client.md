# Contract: LightRAG REST calls made by `kb-sync`

**Date**: 2026-11-21
**Feature**: [../spec.md](../spec.md) | **Research**: [../research.md](../research.md) R1-R4

`kb-sync` talks to LightRAG only through its REST API (FR-002). The server needs no change
except `.env` rerank configuration. Base URL `http://127.0.0.1:${PORT}`, no auth header
(as feature 001's `lightrag-api.md`). Verified against the pinned `v1.5.6` image.

## Spec identifiers and the routes that implement them

| Spec (Verbatim Constraints) | Route used | Note |
|---|---|---|
| Retrieval: `POST /query` with `mode: "mix"` and `only_need_context: true` | same | R1 |
| Ingest: `POST /documents/scan` | same | R2 |
| Removal: `DELETE /documents/{doc_id}` | `DELETE /documents/delete_document` | v1.5.6 has no `/documents/{doc_id}` delete route (plan.md → Spec corrections 1). Spec amendment pending. |

## Calls

### `POST /query` (retrieve, `ask` and `kb_search`)

```json
{"query": "<question>", "mode": "mix", "only_need_context": true,
 "include_references": true, "enable_rerank": true}
```

- `enable_rerank`: `true` for filter `reranker`, `false` for `local` and `none`.
- Response: `response` is LightRAG's context string; `references` is
  `[{reference_id, file_path}]`.
- Parsing: take the fenced JSON block under the heading "Document Chunks", one object per
  line with `reference_id`, `content` and optional `content_headings`. Join to
  `references` by `reference_id`. `file_path` is the staged basename
  `<source>__<source_id>.md`, which splits on `__` to the item record. Entity and
  relationship blocks are discarded.
- A contract test parses a recorded v1.5.6 response; a format change fails it.

### `POST /documents/scan` (ingest)

- Body: none. Returns a `track_id`.
- Scans only the top level of `data/inputs/`, which is why files are staged flat.

### `GET /documents/track_status/{track_id}`

Polled until every document is in a terminal state (`processed` or `failed`).

### `GET /documents/paginated`

Finds a document's ID by matching `file_path` to the staged basename after scan.

### `DELETE /documents/delete_document` (removal)

```json
{"doc_ids": ["<doc_id>"], "delete_file": true, "delete_llm_cache": false}
```

- Returns `status` of `deletion_started`, `busy` or `not_allowed`. `busy` → retry with
  backoff; `not_allowed` → item failed, logged.
- Called only from `ingest --apply` with a same-run backup (FR-010, SC-008).
- `delete_file: true` removes the staged copy and its `__parsed__/` variant only; the
  store file under `data/inputs/<source>/` is untouched.

### `GET /documents/pipeline_status`

Polled until idle after removals and before staging or scanning.

### `GET /health`

Readiness check before `ingest`, `ask`, `pack` and the MCP tools; failure → exit `3`
naming `./scripts/start.sh`.

## Forbidden

- `DELETE /documents` (no path) clears the whole knowledge base. The client exposes no
  function that issues it, and a unit test asserts no code path builds that request.
- Any route not listed above.

## Reranker (consumed by LightRAG, probed by `kb-sync`)

- LightRAG config: `RERANK_BINDING=cohere`, `RERANK_MODEL`, `RERANK_BINDING_HOST`,
  `MIN_RERANK_SCORE` (default `0.6`); model `BAAI/bge-reranker-v2-m3` (GGUF).
- Server: llama.cpp `--reranking` on `127.0.0.1:${RERANKER_PORT}`, request
  `{model, query, documents, top_n}`, response `results[].index` and
  `results[].relevance_score` (Phase 0 check R5).
- `kb-sync` probes the reranker's health endpoint before a `reranker`-filtered query; on
  failure it uses the `local` filter and says so. `scripts/health.sh` probes it when
  `RERANK_BINDING` is not `null`.
