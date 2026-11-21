# Tasks: Personal knowledge pipeline (`kb-sync`)

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

> **Scale note.** 98 tasks across about 100 files (most of them tests) in five areas: the new `sync/` Python
> package, Compose and `.env`, the existing backup/restore/health scripts, owner-run checks
> recorded under `specs/002-kb-sync-knowledge-pipeline/checks/`, and the docs. Watch the
> removal path (T016-T018, T027, T029, T038, T041). It is the only code that deletes knowledge, and
> it must never run without a same-run backup and a healthy server afterwards (SC-008, SC-014).
> Tasks marked **Owner** need a person: a browser sign-in, a judgement, or a wait.

All Python tests run inside the image: `docker compose --profile kb-sync run --rm kb-sync pytest`.
Shell tests use `bats` under `tests/unit/`. Python tests drive the public CLI
(`python -m kb_sync …`) against fake Drive, AppFlowy, NotebookLM, Claude and LightRAG
endpoints (`httpx.MockTransport`). They never assert on SQLite rows directly.

---

## Phase 1: Setup

Files: `.env`, `.env.example`, `.gitignore`, `data/kb-sync/.gitkeep`, `data/models/.gitkeep`,
`packs/.gitkeep`, `compose.yaml` (kb-sync service), `.specify/memory/constitution.md`,
`sync/tests/fixtures/eval.example.yaml`, `data/kb-sync/eval.yaml`

**Wave 1 — independent (different files):**

- [x] **T001** [P] Restore byte identity between `.env` and `.env.example` (they differ at line 97). Move the trailing comment on the commented-out `LLM_BINDING_API_KEY` line onto its own line. Verify with `diff` · .env, .env.example
- [x] **T002** [P] Ignore `data/kb-sync/*`, `data/models/*`, `packs/*` and `secrets/`, each data directory keeping a `!…/.gitkeep`, and create the three `.gitkeep` files · .gitignore, data/kb-sync/.gitkeep, data/models/.gitkeep, packs/.gitkeep
- [x] **T003** [P] Constitution **v1.1.1 PATCH** (must land before Phase 8, Story 6). This builds on v1.1.0, which already satisfies FR-001 (commits d82ce62, 66f71d6). Add a "Public web" row to Recorded Cloud Dependencies: review checks fetch only URLs the owner listed in a pack, and send no knowledge-base content. Extend the Claude row: with the opt-in `--web` flag, the topic name goes to Claude's web search (FR-030). Update the Sync Impact Report, version and Last Amended date · .specify/memory/constitution.md
- [x] **T007** [P] [US2] Commit the eval-set format: entries with `id`, `question`, `in_corpus`, `expected_source` (`<source>:<source_id>`) and `evidence` (a phrase the answer's passages must contain). Include 2 example entries built on public facts · sync/tests/fixtures/eval.example.yaml

**⟶ Wait for Wave 1 to finish, then:**

- [x] **T008** Add the `KB_SYNC_*` keys from contracts/kb-cli.md → Configuration keys (the reranker keys come later, in T098). Add `BACKUP_RETENTION=14` in the script-only section and a commented `KB_SYNC_NOTEBOOKLM_SCOPE` (the owner fills it in `.env` after T076). Keep the two files byte-identical, with every comment on its own line · .env, .env.example
- [ ] **T009** [P] [US2] **Owner** Write the FR-025 test set: about 20 Leadership-folder questions with known answers, plus 3 to 5 questions the corpus cannot answer. Follow the T007 format. This must be done before T051 measures the filters (SC-007) · data/kb-sync/eval.yaml

**⟶ Wait for T008, then:**

- [x] **T010** Add the `kb-sync` service: profile `kb-sync`, `build: ./sync`, `network_mode: host`, user `1000:1000`. Mounts: `./.env:/app/.env:ro`, `./data/inputs`, `./data/kb-sync`, `./packs`, `./secrets`, and `${KB_SYNC_RESEARCH_DIR}` read-only (defaulting to an empty dir). No `data/rag_storage` and no `data/backups` (Invariant 4). No `environment:` block. Give it a rationale header comment in the file's existing style (FR-003) · compose.yaml

---

## Phase 2: Foundational (blocks every story)

Files: `sync/Dockerfile`, `sync/.dockerignore`, `sync/pyproject.toml`,
`sync/kb_sync/{__init__,__main__,cli,config,records,store,lightrag,pull}.py`,
`sync/kb_sync/sources/__init__.py`, `sync/tests/conftest.py`, `scripts/kb.sh`,
`scripts/lib/common.sh`, `scripts/ingest.sh`, `scripts/backup.sh`, `scripts/restore.sh`,
`specs/001-lightrag-knowledge-base/data-model.md`,
`specs/001-lightrag-knowledge-base/contracts/operational-cli.md`

### Tests

Test files: `sync/tests/test_config.py`, `sync/tests/test_records.py`, `sync/tests/test_store.py`,
`sync/tests/test_lightrag.py`, `tests/unit/backup_retention.bats`, `tests/unit/kb_ingest.bats`,
`tests/unit/backup_kbsync.bats`

**Wave 1 — independent (different files), written to fail first:**

- [x] **T012** [P] FR-004: secrets load from `secrets/` and never appear in log output, exception text or `--help`. A missing required `.env` key exits `3` and names the key · sync/tests/test_config.py
- [x] **T013** [P] FR-008 and the "sync interrupted" edge case: one record per item holds every listed field, each item change commits on its own, and run logs are append-only. A removal entry with `outcome=removed` but no `backup_ref` is rejected (SC-008) · sync/tests/test_records.py
- [x] **T014** [P] FR-007: a store file sits at `data/inputs/<source>/<source_id>.md`, its front-matter keys are exactly `title`, `source`, `url`, `modified`, it is written atomically, and the staged name `<source>__<source_id>.md` maps back to its item · sync/tests/test_store.py
- [x] **T015** [P] FR-002 and FR-010: the LightRAG client never sends `DELETE /documents` without a path. `DELETE /documents/delete_document` retries on `busy` and returns only once `pipeline_status` is idle. Scan polling stops at a terminal state. `find_doc` matches on the basename `file_path` · sync/tests/test_lightrag.py
- [x] **T016** [P] FR-033 and SC-013. After 16 successful backups, exactly the 14 newest `kb-<timestamp>.tar.gz` files and their manifests remain. Any other file in `data/backups/` is untouched. An `--output` directory is never pruned. A failed or unverified backup prunes nothing. Each `.removed/` folder keeps its 14 newest files. `BACKUP_RETENTION=0` exits `2` · tests/unit/backup_retention.bats
- [x] **T017** [P] FR-034 and SC-014 (`docker` stubbed on `PATH`). If LightRAG is unhealthy after `backup.sh`, `kb.sh ingest` exits `3`, never calls `kb-sync ingest --apply`, and prints a message naming `./scripts/start.sh`. If the server is stopped before the run, it exits `3` without calling `backup.sh` · tests/unit/kb_ingest.bats
- [x] **T018** [P] FR-005 and the restore edge case: `backup.sh` archives and verifies `data/kb-sync/`. `restore.sh --force` extracts it and moves the current copy aside with `data/rag_storage/`, never deleting it · tests/unit/backup_kbsync.bats

### Implementation

**Wave 1 — independent (different files):**

- [x] **T019** [P] Build files: `python:3.12-slim` pinned by tag, uid 1000, entrypoint `python -m kb_sync`. Pin deps in `pyproject.toml`: `google-api-python-client`, `google-auth-oauthlib`, `httpx`, `anthropic`, `mcp`, `openpyxl`, `PyYAML`, `python-dotenv`, plus `pytest` for tests. Add the shared fake-endpoint fixtures in `conftest.py` · sync/Dockerfile, sync/.dockerignore, sync/pyproject.toml, sync/tests/conftest.py
- [x] **T020** [P] Move the index-stamp writer out of `ingest.sh` into a shared `write_index_stamp`. Add `load_env` defaults for `BACKUP_RETENTION` and the `KB_SYNC_*` keys that `kb.sh` reads · scripts/lib/common.sh
- [x] **T021** [P] Point feature 001's Invariant 2 at 002 data-model → Amended invariants. Point the `backup.sh` safety line ("does not delete older backups") at spec 002 FR-033 · specs/001-lightrag-knowledge-base/data-model.md, specs/001-lightrag-knowledge-base/contracts/operational-cli.md

**⟶ Wait for Wave 1 to finish, then (Wave 2, independent):**

- [x] **T022** [P] Config loader: read `/app/.env` with python-dotenv and the secrets directory. Redacting `repr`. `google_scopes()` returns `drive.readonly`, plus `KB_SYNC_NOTEBOOKLM_SCOPE` when NotebookLM is enabled · sync/kb_sync/__init__.py, sync/kb_sync/__main__.py, sync/kb_sync/config.py
- [x] **T023** [P] Records store per data-model: tables for items, locations, sources, runs, removals, notebooks and notebook sources. Per-item transactions and JSON run logs under `data/kb-sync/runs/` · sync/kb_sync/records.py
- [x] **T024** [P] Store files: front-matter writer, atomic write, staging copy, and moving a removed item's file into `.removed/` · sync/kb_sync/store.py
- [x] **T025** [P] LightRAG REST client per contracts/lightrag-client.md: health, scan, track_status, paginated lookup, delete_document with busy retry and idle wait, pipeline_status, and `query`. No bare-collection delete method · sync/kb_sync/lightrag.py
- [x] **T026** [P] Call `write_index_stamp` from `common.sh` instead of writing the stamp inline · scripts/ingest.sh
- [x] **T027** [P] Always archive `data/kb-sync/`, include it in the manifest and its verification, then prune per FR-033 (default directory only, after verification, log `pruned <path>` to stderr) · scripts/backup.sh
- [x] **T028** [P] Extract `data/kb-sync/` with the knowledge base, and move it aside under `--force` alongside `data/rag_storage/` · scripts/restore.sh
- [x] **T029** [P] New wrapper in the repo idiom (header citing contracts/kb-cli.md, `usage()`, exit codes 0-4). Subcommands pass through `compose --profile kb-sync run --rm kb-sync`. The `ingest` host sequence is: health preflight, `--plan`, `backup.sh` if any removal, `wait_for_health "$READY_TIMEOUT"` (fail → exit `3`, no `--apply`, FR-034), `--apply <plan_id> --backup <archive>`, `write_index_stamp`. Exit `4` when removals are held. Also `nightly` (pull, then the ingest sequence, then the summary), `mcp` (`exec … run --rm -T kb-sync mcp` with nothing on stdout first) and `eval` · scripts/kb.sh

**⟶ Wait for Wave 2 to finish, then:**

- [x] **T030** The full argparse surface from contracts/kb-cli.md (every verbatim command and switch, exit codes 0-4), dispatching lazily to story modules. A `pull` orchestrator with a per-source registry (`drive`, `appflowy`, `research`) that isolates each source's failure and records `auth_expired` with the renew command · sync/kb_sync/cli.py, sync/kb_sync/pull.py, sync/kb_sync/sources/__init__.py

**Checkpoint**: the image builds, `scripts/kb.sh --help` lists every command, and backups carry `data/kb-sync/` with retention. T012-T018 pass.

---

## Phase 3: User Story 1 — Keep chosen Drive folders in the knowledge base (P1) 🎯 MVP

**Goal**: pull the Leadership folder into the store and keep LightRAG in step through edits and deletions.
**Independent Test**: pull and ingest the Leadership folder, then ask a question only one doc answers. Edit that doc and re-run; the answer changes. Delete it and re-run; the knowledge base no longer answers from it.

Files: `sync/kb_sync/sources/drive.py`, `sync/kb_sync/sources/sheets.py`, `sync/kb_sync/ingest.py`,
`sync/tests/fixtures/drive/`, `specs/002-kb-sync-knowledge-pipeline/checks/{drive-oauth,leadership-acceptance}.md`

### Tests

Test files: `sync/tests/test_pull_scope.py`, `test_pull_changes.py`, `test_pull_formats.py`,
`test_pull_duplicates.py`, `test_pull_notebook_outputs.py`, `test_auth_google.py`,
`test_ingest_replace.py`, `test_ingest_removal.py`, `test_ingest_resume.py` (all under `sync/tests/`)

**Wave 1 — independent (different files), written to fail first:**

- [x] **T031** [P] [US1] FR-006: `pull` copies files in allow-listed folders and their subfolders, and ignores a change to a file outside them. Items reachable only from a folder removed from config become removals that are always held (scenario 1, edge case) · sync/tests/test_pull_scope.py
- [x] **T032** [P] [US1] FR-009 and SC-004: with no upstream change, a second `pull` and `ingest --plan` stage zero items. A move or rename keeps the item's ID and store path, and changes only its title, link or locations · sync/tests/test_pull_changes.py
- [x] **T033** [P] [US1] FR-012: a Doc becomes Markdown and a PDF is downloaded. An `.xlsx` or Sheet becomes one Markdown table per sheet. Audio, video and images are listed as skipped. A Doc over the 10 MB export limit is reported as failed with its title and size (scenarios 5 and 6, oversized-Doc edge case) · sync/tests/test_pull_formats.py
- [x] **T034** [P] [US1] FR-013: the same `md5Checksum` in two subfolders gives one staged file and one ingest, with both folder locations recorded (scenario 4) · sync/tests/test_pull_duplicates.py
- [x] **T035** [P] [US1] FR-014: a file matching the NotebookLM output pattern gets source `notebook-output`. A text output is staged. An audio or video output is recorded but never staged · sync/tests/test_pull_notebook_outputs.py
- [x] **T036** [P] [US1] FR-018: with a revoked Google refresh token, `pull` exits `3` with `ERROR: drive sign-in expired. Renew it with scripts/kb.sh auth google` · sync/tests/test_auth_google.py
- [x] **T037** [P] [US1] FR-010 replace and FR-008: for an edited item, `ingest --apply` deletes the old `doc_id` and waits for idle before scanning the new staged copy, then binds the new `doc_id` (scenario 2) · sync/tests/test_ingest_replace.py
- [x] **T038** [P] [US1] FR-010 removal and FR-011. A trashed item is removed only when `--backup` names an archive newer than the plan, and the run log records title, source, reason and `backup_ref` (scenario 3, SC-008). Eleven removals with the limit at 10 hold all of them, still ingest the additions, and exit `4`. `--confirm-removals` then performs them · sync/tests/test_ingest_removal.py
- [x] **T039** [P] [US1] "Sync interrupted" edge case: killing `ingest --apply` after the delete but before the scan leaves the item `stale`, and the next run completes it with no duplicate document · sync/tests/test_ingest_resume.py

### Implementation

**Wave 1 — independent (different files):**

- [x] **T040** [P] [US1] `.xlsx` to Markdown tables (one per sheet, header row, cells escaped) · sync/kb_sync/sources/sheets.py
- [x] **T041** [P] [US1] Ingest planner and applier per data-model → Ingest plan: limit and hold rules, the backup-ref guard, delete then stage then scan then bind, LightRAG failure reasons (including scanned PDFs) recorded per item, and the run summary line from contracts/kb-cli.md · sync/kb_sync/ingest.py
- [x] **T042** [P] [US1] Recorded Drive API responses for the tests: changes pages, a folder tree, an export, a PDF, an xlsx, media and duplicates · sync/tests/fixtures/drive/

**⟶ Wait for Wave 1 to finish, then:**

- [ ] **T005** [US1] **Owner** Phase 0 Google check (R8). Create the OAuth desktop client with `drive.readonly`, published "In production" (unverified). Confirm `text/markdown` is on Drive's Docs export MIME table (else use `text/plain`). Put the client JSON at `secrets/google-client.json` with mode `0600` · specs/002-kb-sync-knowledge-pipeline/checks/drive-oauth.md

**⟶ Wait for T005, then:**

- [x] **T043** [US1] Drive source: `auth google` with the loopback OAuth flow (scopes from `config.google_scopes()`), the changes feed with `includeRemoved`, the allow-list ancestor filter with folder-tree cache, Docs export with fallback, PDF download, Sheets and `.xlsx` via `sheets.py`, `md5Checksum` de-duplication, NotebookLM-output tagging, and media skipping · sync/kb_sync/sources/drive.py

**⟶ Wait for T043, then:**

- [ ] **T044** [US1] **Owner** Leadership acceptance: `kb.sh auth google`, `pull`, then `ingest` overnight. Record every one of the 44 files as ingested, skipped or failed (SC-003). Run the 5-edit and 3-deletion tests (SC-001, SC-002) and a no-change re-run under 5 minutes (SC-004) · specs/002-kb-sync-knowledge-pipeline/checks/leadership-acceptance.md

**Checkpoint**: Drive sync works end to end on the Leadership folder, removals are backed up, logged and held over the limit.

---

## Phase 4: User Story 2 — Ask a question and get a cited answer (P1)

**Goal**: `kb.sh ask` answers from filtered passages with working citation links.
**Independent Test**: run the T009 test set; links open the right source, and out-of-corpus questions get "not in the knowledge base".

Files: `sync/kb_sync/ask.py`, `sync/kb_sync/filters.py`, `sync/kb_sync/citations.py`,
`sync/kb_sync/answer.py`, `sync/kb_sync/evalset.py`, `scripts/health.sh`,
`sync/tests/fixtures/query_context.json`, `specs/002-kb-sync-knowledge-pipeline/checks/{reranker,eval-results}.md`,
and the reranker parts of `.env`, `.env.example` and `compose.yaml` (shared with Phase 1, see Dependencies)

### Reranker setup (Principle IX, runs first)

- [ ] **T004** [US2] Phase 0 reranker check (R5). From the llama.cpp server README, confirm `--reranking`, the `/v1/rerank` path, and the response fields `results[].index` and `relevance_score`. Pin `RERANKER_IMAGE_TAG` and pick `RERANKER_PORT`. Download a GGUF build of `BAAI/bge-reranker-v2-m3` into `data/models/` and record its SHA-256. Score 30 chunks on the CPU in under 2 s. Record the doc URLs and results. If the check fails, switch to Infinity (R5) · specs/002-kb-sync-knowledge-pipeline/checks/reranker.md

**⟶ Wait for T004, then:**

- [ ] **T098** [US2] Add the reranker keys from contracts/kb-cli.md → Configuration keys: `RERANKER_IMAGE_TAG`, `RERANKER_PORT`, `RERANK_BINDING=cohere`, `RERANK_MODEL`, `RERANK_BINDING_HOST`, `MIN_RERANK_SCORE=0.6`, with the values from T004. Keep the two files byte-identical, with every comment on its own line · .env, .env.example

**⟶ Wait for T098, then:**

- [ ] **T011** [US2] Add the `reranker` service as its own reviewable commit (Principle IX). Use the pinned llama.cpp image from T004, CPU only, `--reranking`, `--host 127.0.0.1 --port ${RERANKER_PORT}`, `./data/models:/models:ro`, and a loopback healthcheck. Give `lightrag` `depends_on: reranker` with `required: false`, so starting the knowledge base starts the reranker and a reranker failure never blocks LightRAG · compose.yaml

**⟶ Wait for T011, then:**

### Tests

Test files: `sync/tests/test_ask_retrieve.py`, `test_filters.py`, `test_citations.py`,
`test_answer.py`, `test_ask_switches.py`, `test_evalset.py` (all under `sync/tests/`)

**Wave 1 — independent (different files), written to fail first:**

- [ ] **T046** [P] [US2] FR-019 and scenario 3: `ask` sends LightRAG `mode: "mix"` and `only_need_context: true`, parses the Document Chunks block from the recorded response, and the payload to Claude holds only the question and the surviving passages (no entities, relations or other chunks) · sync/tests/test_ask_retrieve.py
- [ ] **T047** [P] [US2] FR-020 and scenario 6: with the reranker probe failing, `ask` uses local scoring (top `KB_SYNC_LOCAL_TOP_N`, score ≥ 1), and the answer states `filter: local scoring`. With the probe passing, it sends `enable_rerank: true` and states `filter: reranker (min 0.6)` · sync/tests/test_filters.py
- [ ] **T048** [P] [US2] FR-021 and scenario 1: every source line matches `[n] Title (Drive|AppFlowy|NotebookLM|Web|Research) link`, each `[n]` in the body has a source line, and links come from the item records · sync/tests/test_citations.py
- [ ] **T049** [P] [US2] FR-022, scenario 2 and SC-006: when no passage survives, the body is exactly `not in the knowledge base` and the Claude client is never called. When Claude is unreachable, exit `1` with a hint to retry with `--local`, and no retry sends unfiltered context · sync/tests/test_answer.py
- [ ] **T050** [P] [US2] FR-023, scenarios 4 and 5: `--local` never constructs the Claude client and answers through Ollama (SC-009). `--no-filter` sends `enable_rerank: false` and passes every parsed chunk to the answer stage · sync/tests/test_ask_switches.py
- [ ] **T051** [P] [US2] FR-025: `kb.sh eval --filter all` runs each test question with `none`, `reranker` and `local`, prints `<id> <filter> evidence=kept|lost`, then `kept=<n>/<total>` per filter · sync/tests/test_evalset.py

### Implementation

**Wave 1 — independent (different files):**

- [ ] **T052** [P] [US2] Capture a real `POST /query` context-only response as the parser fixture, from a knowledge base holding only `tests/fixtures/sample.md`, so it contains no private text · sync/tests/fixtures/query_context.json
- [ ] **T053** [P] [US2] Filters: reranker probe and `enable_rerank`, local 0-3 scoring via Ollama (`num_predict` 2, temperature 0, sequential), and `none` · sync/kb_sync/filters.py
- [ ] **T054** [P] [US2] Citation numbering per distinct source and label mapping from the source value, resolved through the records by staged basename · sync/kb_sync/citations.py
- [ ] **T055** [P] [US2] Answer stage: an `anthropic` client with the model from `KB_SYNC_CLAUDE_MODEL`, a passages-only system prompt that cites each claim, an Ollama path for `--local`, and an offline error · sync/kb_sync/answer.py

**⟶ Wait for Wave 1 to finish, then:**

- [ ] **T056** [US2] `ask` pipeline: retrieve, parse chunks and join references, filter, number, answer, and print the answer, the filter line and the source list. Expose `retrieve_filtered()` for the MCP server · sync/kb_sync/ask.py

**⟶ Wait for T056, then (independent):**

- [ ] **T057** [P] [US2] FR-025 runner over `data/kb-sync/eval.yaml` · sync/kb_sync/evalset.py
- [ ] **T058** [P] [US2] When `RERANK_BINDING` is not `null`, probe the reranker and report it in text and `--json` output · scripts/health.sh

**⟶ Wait for T057 and T009, then:**

- [ ] **T059** [US2] **Owner** Run `kb.sh eval --filter all` on the T009 set. Record kept counts per filter and whether the chosen filter keeps at least 18 of 20 (SC-007). Check that 100% of cited links open the original (SC-005). Tune `MIN_RERANK_SCORE`. Apply Principle IX: keep the reranker only if it beats local scoring, otherwise record a follow-up change to remove the service · specs/002-kb-sync-knowledge-pipeline/checks/eval-results.md

**Checkpoint**: `kb.sh ask` gives cited answers, refuses out-of-corpus questions without calling Claude, and the filter choice is backed by measured results.

---

## Phase 5: User Story 3 — Query the knowledge base from Claude Code (P2)

**Goal**: Claude Code on this machine can search the knowledge base and open a cited source.
**Independent Test**: in a Claude Code session, search for a Leadership fact and open the cited source.

Files: `sync/kb_sync/mcp_server.py`, `specs/002-kb-sync-knowledge-pipeline/checks/mcp-acceptance.md`

### Tests

Test files: `sync/tests/test_mcp_tools.py`, `sync/tests/test_mcp_errors.py`

**Wave 1 — independent (different files), written to fail first:**

- [ ] **T060** [P] [US3] FR-024, scenarios 1 and 2: over stdio, `kb_search` returns filtered passages, each with title, origin label and link. `kb_get_source` with `<source>:<source_id>` returns title, origin, link, last-modified time and stored text · sync/tests/test_mcp_tools.py
- [ ] **T061** [P] [US3] Scenario 3: with LightRAG stopped, both tools return an error naming the stopped server and `./scripts/start.sh` · sync/tests/test_mcp_errors.py

### Implementation

- [ ] **T062** [US3] FastMCP stdio server with `kb_search` (through `ask.retrieve_filtered()`, no Claude call) and `kb_get_source` per contracts/mcp-tools.md · sync/kb_sync/mcp_server.py

**⟶ Wait for T062, then:**

- [ ] **T063** [US3] **Owner** Register with `claude mcp add kb -- <repo>/scripts/kb.sh mcp`, then search and open a source from a `leesinclair.dev` session · specs/002-kb-sync-knowledge-pipeline/checks/mcp-acceptance.md

**Checkpoint**: Claude Code can search and cite the knowledge base locally.

---

## Phase 6: User Story 4 — Bring AppFlowy notes into the knowledge base (P2)

**Goal**: an AppFlowy workspace's pages and databases sync like Drive.
**Independent Test**: pull a workspace and ingest. A question only an AppFlowy note answers is cited with a link that opens the page.

Files: `sync/kb_sync/sources/appflowy.py`, `sync/kb_sync/sources/appflowy_blocks.py`,
`sync/tests/fixtures/appflowy/`, `specs/002-kb-sync-knowledge-pipeline/checks/{appflowy,appflowy-acceptance}.md`

### AppFlowy check (runs first)

- [ ] **T006** [US4] **Owner** Phase 0 AppFlowy check (R9). Confirm the hosted AppFlowy Cloud base URL and that the GoTrue password grant works, from the AppFlowy API doc page · specs/002-kb-sync-knowledge-pipeline/checks/appflowy.md

**⟶ Wait for T006, then:**

### Tests

Test files: `sync/tests/test_appflowy_blocks.py`, `test_appflowy_pull.py`, `test_appflowy_databases.py`, `test_auth_appflowy.py` (under `sync/tests/`)

**Wave 1 — independent (different files), written to fail first:**

- [ ] **T064** [P] [US4] FR-015 and scenarios 1 and 5: block JSON for headings, paragraphs, lists, to-dos, code, quotes, links and tables converts to matching Markdown. An unknown block type keeps its text and is reported by type · sync/tests/test_appflowy_blocks.py
- [ ] **T065** [P] [US4] FR-015 and scenarios 3 and 4: `pull --source appflowy` walks the page tree. An edited page (newer `last_edited_time`) becomes a replacement, and a trashed page becomes a removal handled as in Story 1 · sync/tests/test_appflowy_pull.py
- [ ] **T066** [P] [US4] FR-016 and scenario 2: a database becomes one Markdown table, and a database listed in `KB_SYNC_APPFLOWY_NOTES_DATABASES` becomes one store file per row · sync/tests/test_appflowy_databases.py
- [ ] **T067** [P] [US4] FR-018: an expired AppFlowy refresh token makes `pull` exit `3`, naming `scripts/kb.sh auth appflowy`, while other sources still pull · sync/tests/test_auth_appflowy.py

### Implementation

- [ ] **T068** [US4] **Owner** Capture 3 to 5 real page JSON documents and one database (rows, fields) from the workspace, scrubbed of private text, as converter fixtures (R9) · sync/tests/fixtures/appflowy/

**⟶ Wait for T068, then:**

- [ ] **T069** [US4] Block-to-Markdown converter · sync/kb_sync/sources/appflowy_blocks.py

**⟶ Wait for T069, then:**

- [ ] **T070** [US4] AppFlowy source: `auth appflowy` (GoTrue password grant, refresh token in `secrets/appflowy.json`), folder walk, page content, databases, trash, and `last_edited_time` change detection · sync/kb_sync/sources/appflowy.py

**⟶ Wait for T070, then:**

- [ ] **T071** [US4] **Owner** Pull and ingest the workspace, ask an AppFlowy-only question, and check the link opens the page · specs/002-kb-sync-knowledge-pipeline/checks/appflowy-acceptance.md

**Checkpoint**: AppFlowy notes answer questions with AppFlowy links.

---

## Phase 7: User Story 5 — Build a research pack and push it to NotebookLM (P3)

**Goal**: a topic goes from a reviewable pack to a populated notebook in one command, and its saved report becomes citable.
**Independent Test**: build and push a pack, save the report to Drive, pull and ingest, and get an answer citing it.

Files: `sync/kb_sync/packs.py`, `sync/kb_sync/notebooklm.py`, `sync/kb_sync/sources/research.py`,
`specs/002-kb-sync-knowledge-pipeline/checks/{notebooklm,notebooklm-acceptance}.md`

### Tests

Test files: `sync/tests/test_packs.py`, `test_notebook_push.py`, `test_notebook_limit.py`, `test_research_source.py` (under `sync/tests/`)

**Wave 1 — independent (different files), written to fail first:**

- [ ] **T072** [P] [US5] FR-026 and scenario 1: `pack <topic>` writes `packs/<topic>.yaml` listing each item's title, origin and link plus web sources, and an owner edit survives a re-run · sync/tests/test_packs.py
- [ ] **T073** [P] [US5] FR-027 and scenarios 2 and 4: `notebook push <topic>` creates the notebook only if it is absent, adds each item as text, PDF or URL, and records each notebook source ID against its item. A changed item is re-added in its new version · sync/tests/test_notebook_push.py
- [ ] **T074** [P] [US5] FR-027 and scenario 3: a pack larger than `KB_SYNC_NOTEBOOK_SOURCE_LIMIT` pushes nothing beyond the limit and lists what was left out · sync/tests/test_notebook_limit.py
- [ ] **T075** [P] [US5] FR-017 and scenario 7: with `KB_SYNC_RESEARCH_DIR` set, `pull` stores its Markdown files with source `research`, and citations use the `Research` label · sync/tests/test_research_source.py

### Implementation

**Wave 1 — independent (different files):**

- [ ] **T076** [P] [US5] **Owner** Phase 0 NotebookLM check (R13): from developers.google.com/notebook/docs, record the OAuth scope (to set `KB_SYNC_NOTEBOOKLM_SCOPE` in `.env`), the base URL, whether sources can be deleted, and the rate limit · specs/002-kb-sync-knowledge-pipeline/checks/notebooklm.md
- [ ] **T077** [P] [US5] Pack builder: suggest items from `ask.retrieve_filtered()` for the topic and merge with the owner's existing file · sync/kb_sync/packs.py
- [ ] **T078** [P] [US5] Local folder source · sync/kb_sync/sources/research.py

**⟶ Wait for T076, then:**

- [ ] **T079** [US5] Thin NotebookLM client and `notebook push`: create/list notebooks, add text, file or URL sources, enforce the source limit, and respect the per-minute create limit · sync/kb_sync/notebooklm.py

**⟶ Wait for T079, then:**

- [ ] **T080** [US5] **Owner** Push one topic, generate the report in NotebookLM, save it to a configured Drive folder, then pull and ingest. The report is tagged `NotebookLM` and cited, and a saved podcast is recorded but not ingested (SC-011, scenarios 5 and 6) · specs/002-kb-sync-knowledge-pipeline/checks/notebooklm-acceptance.md

**Checkpoint**: research packs reach NotebookLM, and NotebookLM outputs are citable.

---

## Phase 8: User Story 6 — Review and prune a topic's notebook (P3)

**Prerequisite**: T003 (constitution v1.1.1) is merged.

**Goal**: a review proposes drops, re-pushes and adds with reasons, and applies only what the owner approved.
**Independent Test**: delete one source upstream, edit another and add a new matching item. The review proposes all three, and applying changes the notebook to match.

Files: `sync/kb_sync/review.py`, `specs/002-kb-sync-knowledge-pipeline/checks/review-acceptance.md`

### Tests

Test files: `sync/tests/test_review_proposal.py`, `test_review_apply.py`, `test_review_web.py` (under `sync/tests/`)

**Wave 1 — independent (different files), written to fail first:**

- [ ] **T081** [P] [US6] FR-028, scenario 1 and SC-012: `review <topic>` writes `packs/<topic>.review.yaml` with one entry per signal (`changed_since_push`, `deleted_upstream`, `url_unreachable`, `stale_uncited`, `near_duplicate`, `new_match`), each with its reason · sync/tests/test_review_proposal.py
- [ ] **T082** [P] [US6] FR-029 and scenarios 2 to 5. `review <topic> --apply` carries out exactly the file as edited. A drop the API cannot perform becomes `pending_manual_drop` and is listed. Adds beyond the source limit are refused, stating the free slots. Re-applying the same proposal is refused · sync/tests/test_review_apply.py
- [ ] **T083** [P] [US6] FR-030: without `--web`, no Claude call is made. With `--web`, only the topic name is sent, and the results appear as `web_search` candidate adds · sync/tests/test_review_web.py

### Implementation

- [ ] **T084** [US6] Review proposal builder and applier per data-model → Review proposal, with web URL checks (HEAD/GET of owner-listed URLs only) and the opt-in Claude web search · sync/kb_sync/review.py

**⟶ Wait for T084, then:**

- [ ] **T085** [US6] **Owner** Run the independent test on a pushed topic and record the proposal and the notebook after apply · specs/002-kb-sync-knowledge-pipeline/checks/review-acceptance.md

**Checkpoint**: notebooks can be pruned with owner approval.

---

## Phase 9: User Story 7 — Unattended nightly sync (P3)

**Prerequisite**: T044 (Leadership acceptance) is done, and the 8-day token gate below passes.

- [ ] **T045** [US7] **Owner** R8 token lifetime: at least 8 days after the first `auth google`, run `pull` without re-auth and record the outcome. If it fails, revisit the Clarifications decision before the nightly job is enabled · specs/002-kb-sync-knowledge-pipeline/checks/drive-token.md

**Goal**: a nightly job pulls and ingests, and the owner reads the result in the morning.
**Independent Test**: enable the timer, edit a Drive doc during the day, and next morning the change is answered and the summary is written.

Files: `sync/kb_sync/nightly.py`, `systemd/kb-sync-nightly.service`, `systemd/kb-sync-nightly.timer`,
`specs/002-kb-sync-knowledge-pipeline/checks/{drive-token,nightly-acceptance}.md`

### Tests

Test files: `sync/tests/test_nightly_summary.py`, `sync/tests/test_nightly_isolation.py`

**Wave 1 — independent (different files), written to fail first:**

- [ ] **T086** [P] [US7] FR-032 and scenarios 1 and 2: the nightly summary reports added, replaced, removed, held, skipped and failed counts. With removals over the limit, none are removed, everything else is ingested, and the held items are listed · sync/tests/test_nightly_summary.py
- [ ] **T087** [P] [US7] FR-032 and scenario 3: with the AppFlowy sign-in expired, Drive still syncs, and the summary names the expired source and `scripts/kb.sh auth appflowy` · sync/tests/test_nightly_isolation.py

### Implementation

**Wave 1 — independent (different files):**

- [ ] **T088** [P] [US7] Nightly summary: merge the run's pull and ingest logs into one summary file and stdout line · sync/kb_sync/nightly.py
- [ ] **T089** [P] [US7] User units: the service runs `scripts/kb.sh nightly` with `SuccessExitStatus=4`, and the timer fires at 02:30 with `Persistent=true` · systemd/kb-sync-nightly.service, systemd/kb-sync-nightly.timer

**⟶ Wait for Wave 1 to finish, then:**

- [ ] **T090** [US7] **Owner** Install and enable the timer (`systemctl --user`), edit a Drive doc, and next morning record the summary and the changed answer · specs/002-kb-sync-knowledge-pipeline/checks/nightly-acceptance.md

**Checkpoint**: the pipeline runs unattended with safe removals.

---

## Phase 10: Polish

Files: `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/TROUBLESHOOTING.md`

**Wave 1 — independent (different files):**

- [ ] **T091** [P] Update the "does not do" exceptions for v1.1.1 (public web checks, opt-in web search) and backup retention, and add `kb.sh` to the command overview · README.md
- [ ] **T092** [P] Bring the out-of-scope list in line with v1.1.x (one-shot kb-sync service, the reranker, the nightly user timer) · CONTRIBUTING.md
- [ ] **T093** [P] Add the 002 operative exceptions, the `kb.sh` commands, the amended data invariants (removals, retention, health wait) and the two-container topology · CLAUDE.md
- [ ] **T094** [P] Document the `kb-sync` and `reranker` services, the new mounts, the staging scheme, and the removal sequence · docs/ARCHITECTURE.md
- [ ] **T095** [P] Cover sign-in and renewal, ingest with backups and retention, MCP registration, timer install, eval, and NotebookLM review · docs/OPERATIONS.md
- [ ] **T096** [P] Cover expired sign-ins, reranker down, the server not returning after a backup (exit 3), held removals (exit 4), and Claude unreachable · docs/TROUBLESHOOTING.md

**⟶ Wait for Wave 1 to finish, then:**

- [ ] **T097** Validate against the Success Criteria. Run pytest in the image, every `tests/unit/*.bats`, `shellcheck scripts/*.sh`, `diff .env .env.example` and `scripts/smoke-test.sh`. Walk SC-001 to SC-014 against the `checks/*.md` records and the run logs (SC-008: zero removals without an earlier same-run backup)

---

## Dependencies & Execution Order

- **Phase 1 Setup**: Wave 1 (T001-T003, T007) → T008 and T009 → T010. No Phase 0 check gates it.
- **Phase 2 Foundational**: needs Phase 1 (T008, T010). Tests T012-T018 first. Then Wave 1 (T019-T021) → Wave 2 (T022-T029) → T030. Blocks every story.
- **Phase 3 US1 (MVP)**: needs Phase 2. Tests T031-T039 → Wave 1 (T040-T042) → T005 (owner) → T043 → T044 (owner).
- **Phase 4 US2**: needs Phase 2 and US1's ingested corpus for T059. T004 → T098 → T011 first, then tests T046-T051 → Wave 1 (T052-T055) → T056 → T057 and T058 → T059 (also needs T009).
- **Phase 5 US3**: needs US2 (`ask.retrieve_filtered`). T060-T061 → T062 → T063.
- **Phase 6 US4**: needs Phase 2. T006 (owner) first, then T064-T067 → T068 → T069 → T070 → T071. It can run alongside US2 and US3 (disjoint files).
- **Phase 7 US5**: needs US1 and US2. T072-T075 → Wave 1 (T076-T078) → T079 → T080.
- **Phase 8 US6**: needs US5 and T003. T081-T083 → T084 → T085.
- **Phase 9 US7**: needs T044 and the T045 token gate, and ideally US4. T045 → T086-T087 → Wave 1 (T088-T089) → T090.
- **Phase 10 Polish**: after the stories it documents. Wave 1 (T091-T096) → T097.

Every file has one owner phase, with one deliberate exception: Phase 1 and Phase 4 both edit `.env`, `.env.example` and `compose.yaml` (kb-sync keys and service in Phase 1, reranker keys and service in Phase 4). Phase 4 starts only after Phase 1 is done, and no phase that runs alongside Phase 4 touches those files. Parallel opportunities: once Phase 2 is done, US2, US4 and the test waves of later stories touch disjoint files and can be built side by side.
