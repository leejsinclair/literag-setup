# Contract: `kb-sync` CLI and `scripts/kb.sh` wrapper

**Date**: 2026-11-21
**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md) | **Research**: [../research.md](../research.md)

The tool is `kb-sync`. Source lives in `sync/`. It runs as a one-shot Compose service under
the `kb-sync` profile, so `./scripts/start.sh` never starts it (FR-003). The owner reaches
every command through the wrapper `scripts/kb.sh` (FR-031). This document fixes the
invocations, exit codes, stdout formats and side effects that tests and the owner code
against. Identifiers in `code` that also appear in the spec's Verbatim Constraints are
copied from it unchanged.

## Shared contract

- **Wrapper idiom**: `scripts/kb.sh` follows feature 001's script contract
  (`specs/001-lightrag-knowledge-base/contracts/operational-cli.md` → "Shared contract"):
  sources `scripts/lib/common.sh`, `set -euo pipefail`, `usage()` to stderr, all Docker
  access through the `compose` wrapper, progress and errors on stderr, results on stdout.
- **Dispatch**: `scripts/kb.sh <subcommand> [args]` runs
  `compose --profile kb-sync run --rm kb-sync <subcommand> [args]`, except where a row
  below says the host does more (`ingest`, `nightly`, `mcp`).
- **Exit codes** (uniform with feature 001):

  | Code | Meaning in `kb-sync` |
  |------|----------------------|
  | `0` | success |
  | `1` | failed: an upstream call failed, a file failed to export or ingest, Claude unreachable |
  | `2` | bad arguments, unknown topic, malformed pack or proposal file |
  | `3` | unmet prerequisite: knowledge-base server stopped, sign-in missing or expired, `.env` key missing, reranker image absent when required |
  | `4` | refused for safety: removals held over `KB_SYNC_REMOVAL_LIMIT`, removal requested without a same-run backup, adds over the notebook source limit |

- **Actionable errors** (Principle VII, FR-018):
  - Server stopped → `ERROR: LightRAG server is not running at http://127.0.0.1:${PORT}. Start it with ./scripts/start.sh` and exit `3`.
  - Sign-in expired or missing → `ERROR: <source> sign-in expired. Renew it with scripts/kb.sh auth <google|appflowy>` and exit `3`.
  - A pre-release API call failing (NotebookLM, AppFlowy) names the HTTP method and path that failed.
- **Secrets** are never printed, logged or written to the run log (FR-004).
- **Config** comes only from `/app/.env` (bind-mounted read-only) and the secrets
  directory. See "Configuration keys" below.

## Commands

### `kb-sync auth google|appflowy`

- **Action**: `google` runs the installed-app OAuth flow with a loopback redirect, scope
  `drive.readonly` (research R8). When `KB_SYNC_NOTEBOOKLM_ENABLED=true` it also requests
  the NotebookLM scope (R13). `appflowy` prompts for email and password and performs the
  GoTrue password grant at `${KB_SYNC_APPFLOWY_BASE_URL}/gotrue/token` (R9).
- **Writes**: `secrets/google-token.json` or `secrets/appflowy.json`, mode `0600`.
- **stdout**: `signed in: <google|appflowy> <account email>`.
- **Exit**: `0` signed in; `1` the provider refused; `2` unknown source.
- **Re-runnable**: this is the renew command named by every expired-sign-in error
  (FR-018, SC-010).

### `kb-sync pull [--source drive|appflowy]`

- **Action**: with no `--source`, pulls every configured source (`drive`, `appflowy`,
  `research`, plus `notebook-output` items found inside the Drive folders). With
  `--source`, pulls that one only. Reads Drive only under `KB_SYNC_DRIVE_FOLDER_IDS` and
  their subfolders (FR-006).
- **Writes**: store files `data/inputs/<source>/<source_id>.md` (see
  [store-formats.md](./store-formats.md)) and item records under `data/kb-sync/`. Marks
  changed items `stale` with pending `replace`, new items `stale` with pending `ingest`,
  and items deleted, trashed or no longer in the allow-list with pending `remove`. Never
  calls LightRAG and works with the server stopped.
- **stdout**: one summary line per source, then a total:

  ```text
  drive      new=3 changed=1 removed=0 unchanged=40 skipped=2 failed=1
  appflowy   new=0 changed=0 removed=0 unchanged=0 skipped=0 failed=0
  total      new=3 changed=1 removed=0 unchanged=40 skipped=2 failed=1
  ```

  Each skipped and failed item is listed on stderr with its title, source and reason
  (FR-012). An empty source prints zeros and is not an error.
- **Exit**: `0` all sources pulled (skips do not count as failures); `1` any item failed
  or any source failed; `3` a sign-in is missing or expired (other sources still pull).

### `kb-sync ingest`

Owner form: `scripts/kb.sh ingest [--confirm-removals]`.

- **Preconditions**: server healthy (else exit `3`).
- **Host sequence** (plan.md → Calls; FR-010, SC-008):
  1. `kb-sync ingest --plan` writes `data/kb-sync/plan.json`.
  2. If the plan holds any removal, the host runs `scripts/backup.sh` and captures the
     archive name it prints.
  3. `kb-sync ingest --apply <plan_id> [--backup <archive name>]` performs removals, then
     stages and scans.
  4. The host writes the index stamp `data/rag_storage/.literag-index.json`, shared with
     `ingest.sh`.
- **Internal subcommands** (container only; not for the owner):
  - `kb-sync ingest --plan` → stdout: the `plan_id`.
  - `kb-sync ingest --apply <plan_id> [--backup <archive name>] [--confirm-removals]` →
    refuses (exit `4`) to remove anything unless `--backup` names an archive created after
    the plan's `created_at`.
- **Removal safety**: if pending removals exceed `KB_SYNC_REMOVAL_LIMIT` (default `10`),
  none are performed and all are held. `--confirm-removals` performs held removals,
  still after a same-run backup (FR-011).
- **stdout** (final line is the machine-readable summary):

  ```text
  added=3 replaced=1 removed=0 held=0 skipped=2 failed=1 backup=<archive name|none>
  ```

- **Exit**: `0` done; `1` any document failed in LightRAG; `3` server stopped; `4` any
  removal held.

### `kb-sync ask "question"`

- **Switches**: `--local`, `--no-filter` (FR-023). They may be combined.
- **Action** (FR-019 to FR-022): retrieve with LightRAG `POST /query`, `mode: "mix"`,
  `only_need_context: true` (see [lightrag-client.md](./lightrag-client.md)); keep chunks
  only; filter; number the survivors; answer.
  - Filter `reranker` (default when `KB_SYNC_FILTER=reranker` and the reranker probe
    passes), else `local` (Ollama scoring, top `KB_SYNC_LOCAL_TOP_N` scoring ≥ 1), or
    `none` with `--no-filter` or `KB_SYNC_FILTER=none`.
  - Answer with Claude (`KB_SYNC_CLAUDE_MODEL`), or with the local model under `--local`,
    sending nothing off the machine.
  - If no passage survives, print the no-answer text and do not call Claude.
- **stdout**:

  ```text
  <answer text, each claim cited as [n]>

  Sources:
  [1] Title (Drive) https://docs.google.com/...
  [2] Title (AppFlowy) https://appflowy.com/...

  Filter: reranker
  ```

  - Each source line matches exactly `[n] Title (Drive|AppFlowy|NotebookLM|Web|Research) link`,
    resolved through the item records (FR-021). Only cited sources are listed.
  - No-answer case: the answer body is exactly `not in the knowledge base`, with no
    `Sources:` block, followed by the `Filter:` line.
  - `Filter:` is one of `reranker`, `local`, `none`. When the reranker was configured but
    unavailable, stderr says so and the line reads `Filter: local (reranker unavailable)`.
- **Exit**: `0` answered (including the no-answer case); `1` Claude unreachable, with the
  hint `retry with --local` (never retries with unfiltered context); `3` server stopped.

### `kb-sync pack <topic>`

- **Action**: builds or refreshes `packs/<topic>.yaml` (FR-026) from items matching the
  topic, chosen with Claude's help. Keeps entries the owner added or removed by hand.
- **stdout**: the pack path, then `items=<n> web=<n>`.
- **Exit**: `0`; `1` Claude unreachable; `3` server stopped.

### `kb-sync notebook push <topic>`

- **Action**: reads `packs/<topic>.yaml`; creates the topic's notebook if absent; adds each
  item as a source (Markdown text up to 1 MB, PDF upload, or web URL); records each
  notebook source ID against its item; re-adds items changed since their last push (FR-027).
  Never pushes past `KB_SYNC_NOTEBOOK_SOURCE_LIMIT`.
- **stdout**: `notebook=<notebook id> added=<n> repushed=<n> unchanged=<n> left_out=<n>`;
  each left-out item is listed on stderr.
- **Exit**: `0` all pushed; `4` some items left out over the limit; `1` a NotebookLM call
  failed (names the call); `3` NotebookLM sign-in missing or `KB_SYNC_NOTEBOOKLM_ENABLED`
  not `true`.

### `kb-sync review <topic>`

- **Action**: writes the proposal file `packs/<topic>.review.yaml` (see
  [store-formats.md](./store-formats.md)) of drops, re-pushes and adds, each with a reason,
  from the Story 6 signals (FR-028). With `--web`, adds candidate web sources from a short
  Claude web search on the topic (FR-030, opt-in). Changes nothing in NotebookLM.
- **stdout**: the proposal path, then `drop=<n> repush=<n> add=<n>`.
- **Exit**: `0`; `2` topic has no pushed notebook.

### `kb-sync review <topic> --apply`

- **Action**: applies only what `packs/<topic>.review.yaml` currently says (FR-029).
  Drops NotebookLM cannot perform are recorded as `pending_manual` and listed for manual
  removal. Refuses adds beyond the free slots.
- **stdout**: `dropped=<n> pending_manual=<n> repushed=<n> added=<n> refused=<n> free_slots=<n>`;
  each `pending_manual` source is listed on stderr with its notebook source title.
- **Exit**: `0`; `4` adds refused over the limit; `2` proposal missing or malformed.

### Host-only subcommands of `scripts/kb.sh`

These have no spec identifier; they serve FR-024, FR-025 and FR-032.

| Invocation | Action | Exit |
|---|---|---|
| `scripts/kb.sh mcp` | `exec compose --profile kb-sync run --rm -T kb-sync mcp`; prints nothing to stdout first (stdout carries MCP). See [mcp-tools.md](./mcp-tools.md). | as the server |
| `scripts/kb.sh nightly` | `pull`, then the full `ingest` host sequence, each source isolated; writes the run summary (FR-032). Never pushes to NotebookLM or calls Claude. | `0`; `1` any failure; `4` removals held (`SuccessExitStatus=4` in the unit) |
| `scripts/kb.sh eval [--filter none\|reranker\|local\|all]` | runs the FR-025 test set; per question prints `<id> <filter> evidence=kept\|lost`, then `kept=<n>/<total>` per filter. | `0`; `3` server stopped |

## Configuration keys (`.env`, FR-004)

Added to `.env` and `.env.example`, byte-identical, each comment on its own line.

| Key | Default | Meaning |
|---|---|---|
| `KB_SYNC_DRIVE_FOLDER_IDS` | `1qIMoMk98MsRmIJvLsfqo5Pv7f7aKlKRk` | comma-separated Drive folder allow-list (first corpus: Leadership) |
| `KB_SYNC_APPFLOWY_BASE_URL` | Phase 0 (R9) | hosted AppFlowy Cloud base URL |
| `KB_SYNC_APPFLOWY_WORKSPACE_ID` | empty | chosen workspace; empty disables the source |
| `KB_SYNC_APPFLOWY_NOTES_DATABASES` | empty | comma-separated database IDs stored one file per row |
| `KB_SYNC_RESEARCH_DIR` | empty | host path to `leesinclair.dev/research/`; empty disables the source |
| `KB_SYNC_CLAUDE_MODEL` | `claude-sonnet-5-5` | answer-stage model |
| `KB_SYNC_FILTER` | `reranker` | `reranker`, `local` or `none` |
| `KB_SYNC_LOCAL_TOP_N` | `8` | passages kept by local scoring |
| `KB_SYNC_REMOVAL_LIMIT` | `10` | removals per run above which all are held |
| `KB_SYNC_NOTEBOOKLM_ENABLED` | `false` | gates NotebookLM scope and commands |
| `KB_SYNC_NOTEBOOK_SOURCE_LIMIT` | `50` | per-notebook source limit |
| `KB_SYNC_REVIEW_STALE_MONTHS` | `12` | candidate-drop age in reviews |
| `RERANKER_IMAGE_TAG` | pinned in Phase 0 (R5) | llama.cpp server image tag |
| `RERANKER_PORT` | Phase 0 (R5) | reranker loopback port |
| `RERANK_BINDING` | `cohere` | LightRAG rerank binding (`RERANK_BINDING=cohere`) |
| `RERANK_MODEL` | `bge-reranker-v2-m3` | GGUF build of `BAAI/bge-reranker-v2-m3` |
| `RERANK_BINDING_HOST` | `http://127.0.0.1:${RERANKER_PORT}/v1/rerank` | reranker endpoint |
| `MIN_RERANK_SCORE` | `0.6` | LightRAG drops chunks below this |

Secrets live in `secrets/` (gitignored, directory `0700`, files `0600`), mounted read-write
into the `kb-sync` container only: `google-client.json`, `google-token.json`,
`appflowy.json`, `anthropic.key`.
