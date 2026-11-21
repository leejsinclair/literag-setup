# Research: Personal knowledge pipeline (`kb-sync`)

**Date**: 2026-11-21 | **Plan**: [plan.md](./plan.md)

Entries R1 to R4 were verified by reading the pinned image `ghcr.io/hkuds/lightrag:v1.5.6`
(`/app/lightrag/api/routers/*.py`, `operate.py`, `prompt.py`, `rerank.py`, `api/config.py`).
Entries marked **Phase 0 check** are not yet verified. Each names the check and what happens
if it fails. The spec puts them before the story that needs them.

## R1. Retrieving context without an answer

**Decision**: `POST /query` with `mode: "mix"`, `only_need_context: true`,
`include_references: true`, and `enable_rerank` set per filter. The `response` field is
LightRAG's context string. kb-sync parses the fenced JSON block under the heading
"Document Chunks", one object per line with `reference_id` and `content` (plus optional
`content_headings`). It joins the parsed chunks to the top-level `references` list
(`reference_id` → `file_path`). Only chunks become passages. Entity and relationship
blocks are dropped, because they carry no reliable per-source citation.

**Rationale**: this matches the spec's pinned retrieval call (`operate.py` returns the
context string when `only_need_context` is set). The `kg_query_context` template in
`prompt.py` fixes the chunk block's layout. A contract test parses a recorded response, so
a format change fails loudly on `update.sh`.

**Alternatives considered**: `POST /query/data` returns the same retrieval as structured
JSON (entities, relationships, chunks, references) and would need no parsing. It is the
fallback if parsing proves brittle, but it differs from the spec's pinned identifier, so
switching needs a spec amendment.

## R2. Getting store files ingested

**Decision**: the store keeps the pinned path `data/inputs/<source>/<source_id>.md`.
Ingest copies each new or changed store file to `data/inputs/<source>__<source_id>.md`
(flat), calls `POST /documents/scan`, polls `GET /documents/track_status/{track_id}`, then
finds the document ID through `GET /documents/paginated` by matching `file_path` (stored
as the basename). After processing, LightRAG moves the staged copy into
`data/inputs/__parsed__/`.

**Rationale**: `DocumentManager.iter_new_files` uses a single `os.scandir(input_dir)` and
skips every directory, so subfolders are never scanned. LightRAG stores `file_path` as the
canonical basename, so the flat name doubles as a deterministic key back to the record.
`__` cannot occur in a source value, so the name splits unambiguously.

**Alternatives considered**: `POST /documents/upload` or `/documents/text` would avoid
staging, but the spec pins `/documents/scan` and the scan path is what `ingest.sh` already
uses. Moving the store out of `data/inputs/` contradicts the pinned location.

## R3. Removing knowledge

**Decision**: `DELETE /documents/delete_document` with
`{"doc_ids": [<id>], "delete_file": true, "delete_llm_cache": false}`. The call is
asynchronous and returns `deletion_started`, `busy` or `not_allowed`. kb-sync retries on
`busy` with backoff, then polls `GET /documents/pipeline_status` until the pipeline is idle
before it stages or scans anything. The client exposes no way to call `DELETE /documents`.

**Rationale**: these are the only document-removal routes in v1.5.6's
`document_routes.py`. `delete_file: true` removes the staged copy and its `__parsed__`
variant by basename (`delete_file_variants_by_file_path`). It looks only in `INPUT_DIR` and
`__parsed__/`, so the store file in its subfolder is untouched. Deleting before scanning is
required, not tidy: the scan skips a `file_path` it already holds, so an edited file at the
same staged name would otherwise never be re-ingested.

**Alternatives considered**: the spec's `DELETE /documents/{doc_id}` does not exist.
`DELETE /documents` with no path clears every document, so it is never used.

## R4. The reranker filter inside LightRAG

**Decision**: set `RERANK_BINDING=cohere`, `RERANK_MODEL=bge-reranker-v2-m3`,
`RERANK_BINDING_HOST=http://127.0.0.1:${RERANKER_PORT}/v1/rerank` and `MIN_RERANK_SCORE=0.6`
in `.env`. LightRAG reads these from its mounted `/app/.env` (`api/config.py`), so the
server needs no change. kb-sync passes `enable_rerank: true` for the reranker filter and
`false` for `local` and `none`. Before each query it probes the reranker's health endpoint,
and if the probe fails it switches to local scoring and says so.

**Rationale**: `lightrag_server.py` maps `cohere` to `rerank.cohere_rerank`, which posts
`{model, query, documents, top_n}`. `utils.py` drops chunks scoring below
`min_rerank_score`. Rerank applies to chunks only, which are exactly the passages kb-sync
keeps (R1). The probe matters because a failing rerank call would otherwise pass
unfiltered chunks through without telling anyone.

**Alternatives considered**: kb-sync calling the reranker itself. That duplicates
LightRAG's binding, and the Web UI would not benefit.

## R5. Reranker server (Phase 0 check)

**Decision**: the llama.cpp server image `ghcr.io/ggml-org/llama.cpp`, tag pinned in
`RERANKER_IMAGE_TAG`, run CPU-only with `--reranking`, a GGUF build of
`BAAI/bge-reranker-v2-m3` in `data/models/`, and `--host 127.0.0.1 --port ${RERANKER_PORT}`
under host networking. The `lightrag` service gets `depends_on: reranker` with
`required: false`, so starting the knowledge base starts the reranker, and a reranker
failure never blocks LightRAG.

**Rationale**: a single static binary with a small CPU image. It exposes a rerank endpoint
in the Jina/Cohere request shape, which `cohere_rerank` speaks. It needs no Python runtime
or GPU. **Check**: from the llama.cpp server README, confirm the `--reranking` flag, the
`/v1/rerank` path and the response's `results[].index` and `relevance_score`, then score one
query on this box under 2 s for 30 chunks. Record the doc URL here.

**Alternatives considered**: Hugging Face TEI serves `/rerank` in its own format, not
Cohere's, so it would need a shim. vLLM is Cohere-compatible but heavy on CPU. Infinity is
Cohere-compatible and is the next candidate if llama.cpp fails the check.

## R6. Fallback filter: local scoring

**Decision**: for each chunk, one Ollama `/api/generate` call to `LLM_MODEL` asking for a
single digit 0 to 3, with `num_predict` 2 and temperature 0. Keep the top
`KB_SYNC_LOCAL_TOP_N` (8) chunks scoring at least 1. Calls run one at a time.

**Rationale**: matches the spec's assumption. Ollama runs with `OLLAMA_NUM_PARALLEL=1`, so
parallel calls would only queue. A query during a nightly ingest waits its turn. That is
acceptable because the nightly job runs outside working hours.

**Alternatives considered**: scoring every chunk in one prompt is cheaper, but a 3B model
loses track of the chunk numbers.

## R7. Answer stage

**Decision**: the `anthropic` Python SDK. The model comes from `KB_SYNC_CLAUDE_MODEL`
(default `claude-sonnet-5-5`) and the API key from the secrets directory. The system prompt
says to answer only from the numbered passages, cite every claim as `[n]`, and say
"not in the knowledge base" if the passages do not answer. `--local` sends the same prompt
to Ollama. If the API cannot be reached, `ask` exits 1 and suggests `--local`. It never
retries with more context.

**Rationale**: FR-019, FR-022 and FR-023, and the "answer stage offline" edge case.
Isolating the call in `answer.py` keeps the model replaceable (Principle IV).

## R8. Google Drive (Phase 0 check for token lifetime)

**Decision**: an installed-app OAuth flow with a loopback redirect. The container runs on
host networking, so its local redirect server is reachable from the owner's browser.
Scope `drive.readonly`. The refresh token is stored in `secrets/google-token.json`.
Changes come from `changes.getStartPageToken` once, then `changes.list` with
`includeRemoved=true`, saving `newStartPageToken` per source. Each change is filtered to
files whose ancestors include an allow-listed folder; kb-sync caches the folder tree and
walks parents for unknown files. Docs export as `text/markdown` with a `text/plain`
fallback. PDFs download with `alt=media`. `.xlsx` files and Google Sheets (exported as xlsx)
become one Markdown table per sheet via `openpyxl`. Duplicates are found by `md5Checksum`.

**Check**: an "In production", unverified app keeps its refresh token for more than 7 days.
Verify by re-running `pull` 8 days after sign-in during Story 1. If the token dies, revisit
the Clarifications decision before Story 4. Also confirm `text/markdown` is on Drive's
export MIME table, otherwise use `text/plain`.

**Alternatives considered**: `drive.file` with the Picker, rejected in Clarifications.
A full folder walk every run instead of the changes feed is too slow for SC-004 as the
corpus grows.

## R9. AppFlowy Cloud (Phase 0 check)

**Decision**: GoTrue password grant at `${KB_SYNC_APPFLOWY_BASE_URL}/gotrue/token`, with
the refresh token stored in `secrets/appflowy.json`. The folder walk uses
`GET /api/workspace/{id}/folder`. Page content comes from
`GET /api/workspace/v1/{id}/collab/{object_id}/json`. Databases use the `database/{id}/row`,
`row/detail` and `fields` routes, and trash comes from `GET /api/workspace/{id}/trash`.
Change detection compares `last_edited_time`.

**Check**: confirm the hosted base URL and sign-in method against the AppFlowy API doc
page. Capture 3 to 5 real pages as converter fixtures before writing `appflowy_blocks.py`.

## R10. MCP server for Claude Code

**Decision**: `kb-sync mcp` runs a stdio server built with the `mcp` SDK's `FastMCP`.
Claude Code launches it as `scripts/kb.sh mcp`, which `exec`s
`compose --profile kb-sync run --rm -T kb-sync mcp`. `kb.sh` prints nothing to stdout
before the `exec`, because stdout carries the protocol. Owners register it at user scope
with `claude mcp add kb -- <repo>/scripts/kb.sh mcp`, so sessions in `leesinclair.dev` can
use it.

**Rationale**: keeps the server containerised (Principle II) with no long-running process.
Container start-up costs about a second, once per session.

## R11. Records store

**Decision**: one SQLite file `data/kb-sync/kb-sync.db` (stdlib `sqlite3`, WAL off), plus
a JSON run log per run under `data/kb-sync/runs/`. Each item's state change commits in
its own transaction.

**Rationale**: the draft named SQLite, and it gives atomic per-item commits for the
"sync interrupted" edge case. `backup.sh` stops every service before archiving, and
kb-sync is one-shot, so the file is never open during a backup.

## R12. Nightly job

**Decision**: `systemd/kb-sync-nightly.timer` (02:30, `Persistent=true`) starts
`kb-sync-nightly.service`, which runs `scripts/kb.sh nightly`. The service sets
`SuccessExitStatus=4`, so held removals do not mark the run failed. The job only pulls and
ingests. It never pushes to NotebookLM or calls Claude, which keeps Principle III's
"owner-run command" rule for outbound content intact.

**Rationale**: the repo already ships a systemd user unit (`ollama/ollama.service`), and
linger is enabled. Each source's pull runs in its own try block. A sign-in failure is
recorded as `auth_expired` with the renew command, and the other sources continue.

## R13. NotebookLM consumer API (Phase 0 check, before Story 5)

**Decision**: a thin `notebooklm.py` client covering only notebook create/list and source
create (text up to 1 MB, file upload, URL). Source delete is attempted only if the docs
list it. Otherwise drops are recorded as `pending_manual`.

**Check**: from developers.google.com/notebook/docs, confirm the OAuth scope, the base
URL, source deletion, and the 20 source creates per minute limit. If a second Google scope
is needed, `kb.sh auth google` requests it only when `KB_SYNC_NOTEBOOKLM_ENABLED=true`.
