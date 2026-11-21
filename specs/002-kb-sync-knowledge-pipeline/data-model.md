# Phase 1 Data Model: Personal knowledge pipeline (`kb-sync`)

**Date**: 2026-11-21
**Feature**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md) | **Research**: [research.md](./research.md)

Feature 001 had no schema of its own. Its data model mapped entities onto files that LightRAG
owns. This feature adds state that kb-sync owns: one SQLite file (`data/kb-sync/kb-sync.db`,
R11), a JSON log per run, an ingest plan file, and owner-editable YAML under `packs/`. LightRAG's
storage under `data/rag_storage/` stays opaque. kb-sync reaches it only through the REST
routes in R1 to R4.

Legend: **[spec]** marks a pinned value from the spec's Verbatim Constraints. **[plan]** and
**[R*n*]** mark a decision from plan.md or research.md. **[new]** marks a decision made here.
Each [new] decision can change before tasks are written.

---

## Entity overview

```text
 Configuration (.env)  ── defines ──►  Source ──1:N──► Item record ──1:N──► Item location
 Secrets (secrets/)    ── signs in ──►   │                 │  │  │
                                         │ cursor          │  │  └─1:0..1─► Store file ──stage──► Staged copy
                                         ▼                 │  │                                   │ scan
                                      Run log ◄── logs ────┤  └─ doc_id ──► LightRAG document ◄────┘
                                       │  └─1:N─► Removal entry               (opaque, REST only)
                                       ▼                   │
                                 Ingest plan (one at a time)
                                                           │ N:M (topics)
 Research pack (packs/<topic>.yaml) ──1:1──► Notebook ──1:N──► Notebook source ──N:1──► Item record
 Review proposal (packs/<topic>.review.yaml) ── applies to ──► Notebook

 Passage, Answer: in memory only (ask, kb_search). They resolve back to Item records.
 Backup artifact (reshaped from 001): now carries data/kb-sync/ next to data/rag_storage/.
```

---

## Entity: Source

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Source; FR-004, FR-006, FR-009, FR-015, FR-017, FR-018, FR-032 |
| Storage | SQLite table `sources` [plan]. Config comes from `.env`, credentials from `secrets/` |
| Identity | `source_key`, one of `drive`, `appflowy`, `research` [new]. These are the pullable sources. `notebook-output` and `web` are **source values on items**, not pullable sources (see Item record) |
| Fields | `source_key` · `config_fingerprint` (SHA-256 of the `.env` keys that define the source, such as the folder allow-list) · `cursor` (Drive: the `newStartPageToken` from `changes.list` [R8]. AppFlowy: highest `last_edited_time` seen [R9]. Research: none, it compares mtimes) · `auth_state` (`ok`, `missing`, `expired`) · `last_pull_at` · `last_pull_run_id` · `last_error` (text, never holds a secret) |
| Validation | `drive`: `KB_SYNC_DRIVE_FOLDER_IDS` is non-empty and each ID is a folder the account can read. An empty folder is not an error (edge case "Empty source"). `appflowy`: base URL and workspace ID are set. `research`: the path exists and the container can read it |
| Change tracking | The `cursor` moves forward only after every change it covers has been committed to Item records. An interrupted pull replays from the old cursor, and the per-item upsert is idempotent (edge case "Sync interrupted") |
| Config change | If `config_fingerprint` differs from the stored value, the next pull walks the whole allow-list again instead of trusting the cursor. Items reachable only from removed folders get `pending=remove` with reason `out_of_allow_list`, and are always held (see Invariant 6) |
| Auth failure | `auth_state=expired` with the renew command (`scripts/kb.sh auth google` or `… auth appflowy`) recorded in the run log (FR-018, Story 7 scenario 3). Other sources continue |

---

## Entity: Item record

The core record. There is one per upstream item, and it ties the original to its store file,
its LightRAG document, its topics and its notebook sources (FR-008).

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Item record; FR-007 to FR-014, FR-016, FR-021 |
| Storage | SQLite table `items` [plan] |
| Identity | `item_id` (integer PK) plus unique `(source, source_id)`. Identity survives renames and moves (edge case "Moved or renamed upstream") |
| `source` [spec] | `drive` · `appflowy` · `notebook-output` · `web` · `research`. Drives the citation label: `drive`→`Drive`, `appflowy`→`AppFlowy`, `notebook-output`→`NotebookLM`, `web`→`Web`, `research`→`Research` (FR-021) |
| `source_id` | Drive: file ID. AppFlowy: view/object UUID, or the row ID for a database marked as notes. Notebook output: its Drive file ID. Web: SHA-256 (first 16 hex chars) of the normalised URL [new]. Research: SHA-256 (first 16 hex chars) of the POSIX path relative to `KB_SYNC_RESEARCH_DIR` [new]. Must match `[A-Za-z0-9_-]+` |
| Upstream fields | `title` · `url` (opens the original; for research, a `leesinclair.dev` repo path or `file://` URL [new]) · `modified` (UTC ISO-8601, upstream last-modified) · `mime_type` · `size_bytes` · `upstream_md5` (Drive `md5Checksum` if present) |
| Content fields | `fingerprint`: SHA-256 of the item's content as stored, meaning the store-file body without front-matter, or the raw bytes for a PDF [new]. A title or link change alone leaves the fingerprint unchanged, so it triggers no re-ingest |
| Store fields | `store_path` (relative to repo root, e.g. `data/inputs/drive/<id>.md`) · `staged_name` (`<source>__<source_id>.<ext>`) |
| KB fields | `doc_id` (LightRAG document ID, bound after scan by matching `file_path` == `staged_name` via `GET /documents/paginated` [R2]) · `ingested_fingerprint` (the fingerprint that `doc_id` holds) · `ingested_at` |
| `status` [spec] | `active` · `stale` · `removed` (see State transitions) |
| `pending` [plan] | `none` · `ingest` · `replace` · `remove`. Internal. The next ingest's action |
| `held` | boolean. A removal waiting for owner confirmation (FR-011) |
| `removal_reason` | `deleted_upstream` · `trashed_upstream` · `out_of_allow_list` (set when `pending=remove`) |
| `disposition` [new] | `ingest` (default) · `skip_media` (audio, video, images: listed as skipped, FR-012) · `record_only` (NotebookLM audio or video output, FR-014) · `duplicate` (FR-013). Only `ingest` items are staged |
| `duplicate_of` | `item_id` of the canonical item when `disposition=duplicate`, otherwise null |
| `failure` | null, or `{stage: export\|convert\|ingest, reason, fingerprint, at}`. An item whose `failure.fingerprint == fingerprint` is not retried, so SC-004 holds with known failures present. It is retried once the content changes |
| Convert report | `unknown_blocks`: list of AppFlowy block types kept as plain text (FR-015, Story 4 scenario 5) |
| Usage | `last_cited_at`: set when `ask` or `kb_search` cites the item. Feeds the review's "never cited" signal [new] |
| Relationships | 1:N Item location · 0..1 Store file · 0..1 LightRAG document · N:M topics (table `item_topics(item_id, topic)`) · 1:N Notebook source |

**Validation rules**

- A pull writes an item only if at least one of its locations is inside the allow-list (FR-006).
  A change outside it is dropped before any download.
- Supported for ingest: Google Docs (exported as `text/markdown`, falling back to `text/plain`),
  PDFs, `.xlsx` and Google Sheets (one Markdown table per sheet), `.md` and `.txt`, AppFlowy
  documents and databases (FR-012, FR-015, FR-016, R8). MIME types `audio/*`, `video/*` and
  `image/*` → `skip_media`. Anything else → `failure.stage=convert` with reason
  `unsupported type <mime>`.
- A Google Doc that fails to export because of size → `failure.stage=export`, reason recording
  its size (edge case "Oversized Google Doc").
- A PDF with no extractable text fails in LightRAG's pipeline → `failure.stage=ingest` with
  LightRAG's reason (Story 1 scenario 5). There is no OCR.
- Duplicate detection (FR-013): when a new or changed `ingest` item's fingerprint equals that of
  another active `ingest` item, the item with the lower `item_id` stays canonical. The other gets
  `disposition=duplicate`, and its locations are reported against the canonical item. When the
  canonical item is removed, the oldest duplicate is promoted (`disposition=ingest`,
  `pending=ingest`) in the same transaction.
- Notebook output detection (FR-014): a Drive file whose name matches
  `KB_SYNC_NOTEBOOKLM_OUTPUT_PATTERN` [new] gets `source=notebook-output` on its first pull. It
  keeps that value for life, because identity must not move. Text outputs → `ingest`. Audio and
  video outputs → `record_only`.
- `web` items are created only by packs and reviews. They have no store file and no `doc_id`,
  and they are never ingested.

---

## Entity: Item location

| Aspect | Detail |
|--------|--------|
| Spec ref | FR-013; Story 1 scenario 4; edge cases "Folder removed from config", "Moved or renamed upstream" |
| Storage | SQLite table `locations` [plan] |
| Fields | `item_id` · `container_id` (Drive folder ID, AppFlowy parent view ID, research subdirectory) · `container_path` (human path, e.g. `Leadership/Hiring`) · `allow_root` (the configured folder ID or workspace this location descends from) · `first_seen_run_id` |
| Identity | unique `(item_id, container_id)` |
| Rules | Rewritten as a set on every pull that touches the item. An item with zero locations inside the current allow-list is out of scope → `pending=remove`, `removal_reason=out_of_allow_list`. Duplicates' locations are listed against their canonical item in run summaries and in `kb_get_source` |

---

## Entity: Store file

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Store file; FR-007 |
| Path [spec] | `data/inputs/<source>/<source_id>.md` |
| Format | YAML front-matter with exactly the keys [spec] `title`, `source`, `url`, `modified`, then the Markdown body |
| Writer | `pull` only. It is written atomically (temp file, then rename), and only when the fingerprint or front-matter changes |
| PDFs (open decision) [new] | FR-007 requires Markdown, but the plan adds no PDF text extractor, and R8 downloads PDFs whole for LightRAG to parse. Proposed: keep the PDF at `data/inputs/<source>/<source_id>.pdf` and put its provenance in the Item record only. Then the scanned-PDF failure comes from LightRAG, and notebook push can upload the original PDF (FR-027). The alternative is a `pypdf` dependency that extracts text into a `.md`. **Owner decision needed before Story 1 tasks** |
| Never scanned directly | `POST /documents/scan` reads only the top level of `data/inputs/` [R2], so store files in subfolders are invisible to LightRAG |
| Removal | When an item becomes `removed`, its store file is moved to `data/inputs/<source>/.removed/<source_id>.<ext>` [new], not deleted. This keeps Principle V for inputs. Each `.removed/` folder keeps the 14 most recently moved files (`BACKUP_RETENTION`), and `backup.sh` prunes older ones after each successful backup (FR-033) |

## Entity: Staged copy

| Aspect | Detail |
|--------|--------|
| Spec ref | FR-002, FR-010; plan "Calls"; R2, R3 |
| Path | `data/inputs/<source>__<source_id>.<ext>` (flat). After processing, LightRAG moves it to `data/inputs/__parsed__/` |
| Lifetime | Created by `ingest --apply` just before `scan`. Deleted by LightRAG through `delete_document` with `delete_file: true` when the item is replaced or removed [R3] |
| Key back to record | LightRAG stores `file_path` as this basename. Split on the **first** `__` to get `(source, source_id)`. No source value contains `_`, so the split is unambiguous even if a Drive ID itself contains `__` |
| Coexistence with `ingest.sh` | If the owner runs 001's `ingest.sh` while staged copies are waiting, LightRAG ingests them without kb-sync binding a `doc_id`. The next `kb.sh ingest` binds them by `file_path` lookup, then moves them to `active` |

---

## Entity: Ingest plan

| Aspect | Detail |
|--------|--------|
| Spec ref | FR-010, FR-011; SC-008; plan "Calls" |
| Path | `data/kb-sync/plan.json`. There is at most one, and a new `--plan` overwrites an unapplied one |
| Fields | `plan_id` (UUID) · `created_at` (UTC) · `records_version` (a counter in SQLite, bumped on every item commit) · `removals[] {item_id, doc_id, title, source, reason}` · `replacements[] {item_id, old_doc_id}` · `stages[] {item_id, staged_name, fingerprint}` · `held[] {item_id, title, reason}` · `removal_limit` · `applied_at` (null until applied) |
| Build rules | Removals are the items with `pending=remove` and `held=false`, plus the old documents of items with `pending=replace`. Both count toward `KB_SYNC_REMOVAL_LIMIT`. If the count exceeds the limit, **every** removal is moved to `held[]`. Replacements whose old document would be removed are then held too, and they stay `stale` (FR-011, Story 7 scenario 2). `out_of_allow_list` removals are always held. New items (`pending=ingest`) are never held |
| Apply guards | `--apply <plan_id>` refuses (exit 4) if: the plan ID does not match, `applied_at` is set, `records_version` has moved, or the plan has removals or replacements and `--backup` does not name an archive in the host's `data/backups/` whose timestamp is later than `created_at`. The host script passes the archive name, because the container cannot see `data/backups/` |
| Confirmation | `kb.sh ingest --confirm-removals` builds a plan with `held=false` for the currently held items. The backup and apply guards are unchanged |

---

## Entity: Run log and removal entry

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Run log; FR-010, FR-011, FR-032; SC-008; Story 7 |
| Storage | SQLite tables `runs` and `removals` [plan], plus `data/kb-sync/runs/<run_id>.json` (the human-readable summary [R11]) |
| Run fields | `run_id` (UTC timestamp + short random suffix) · `kind` (`pull`, `ingest`, `nightly`, `push`, `review-apply`) · `started_at` · `finished_at` · `exit_code` · `plan_id` · `backup_ref` (archive name or null) · `per_source[] {source_key, outcome: ok\|empty\|auth_expired\|failed, renew_command, error}` · counts `added`, `replaced`, `removed`, `held`, `skipped`, `failed` · `items[] {item_id, title, source, action, reason}` |
| Removal entry fields | `run_id` · `item_id` · `title` · `source` · `doc_id` · `reason` · `backup_ref` · `outcome` (`removed`, `held`, `failed`) · `at` |
| Validation | A removal entry with `outcome=removed` has a non-null `backup_ref`, and that backup's timestamp is earlier than the entry's `at` and belongs to the same `kb.sh` run (Invariant 5, SC-008). Run logs are append-only, and nothing in kb-sync updates or deletes a past run |
| Content rules | No secret, token or passage text is written to a run log (FR-004) |

---

## Entity: Research pack

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Research pack; FR-026, FR-027; Story 5 |
| Path [spec] | `packs/<topic>.yaml` (gitignored, owner-editable) |
| Topic | slug `^[a-z0-9][a-z0-9-]{0,62}$` [new]. The same string is the key in `item_topics`, `notebooks` and the review file name |
| Fields | `topic` · `title` (notebook display name) · `notebook_id` (filled in by the first push) · `items[] {ref, title, source, url}`, where `ref` is `<source>:<source_id>` and `title`, `source` and `url` are copied in for readability (`kb-sync` re-resolves them from records on push) · `web[] {url, title, note}` · `generated_at` · `generated_by` (`claude` or `owner`) |
| Validation | Each `ref` resolves to an Item record with `status=active` and `disposition` of `ingest` or `record_only`. Unknown refs and removed items are rejected by name. Web URLs must be `https://`. Duplicate refs or URLs collapse to one entry. Free-form owner edits are allowed, and unknown keys are kept |
| Effect | On push, `item_topics` is rewritten to match the pack's `items[]` and the `web` entries become `web` Item records |

## Entity: Notebook

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Notebook; FR-027, FR-029; Story 5 scenario 3, Story 6 scenario 5 |
| Storage | SQLite table `notebooks` [new] |
| Fields | `topic` (PK) · `notebook_id` (NotebookLM) · `title` · `source_limit` (from `KB_SYNC_NOTEBOOK_SOURCE_LIMIT`, default 50) · `created_at` · `last_push_at` · `last_review_at` |
| Derived | `used_slots` = count of Notebook sources in state `active` or `pending_manual_drop`. `free_slots` = `source_limit − used_slots` |
| Validation | A push or apply never exceeds `source_limit`. Items past the limit are not pushed and are listed (Story 5 scenario 3). An apply whose adds exceed `free_slots` refuses the excess and reports the free count (exit 4) |

## Entity: Notebook source

| Aspect | Detail |
|--------|--------|
| Spec ref | FR-008 ("per-topic notebook source IDs"), FR-027, FR-029 |
| Storage | SQLite table `notebook_sources` [new] |
| Fields | `topic` · `item_id` · `notebook_source_id` · `kind` (`text`, `pdf`, `url`) · `pushed_fingerprint` · `pushed_at` · `state` (`active`, `pending_manual_drop`, `dropped`) |
| Identity | unique `(topic, item_id)` among rows not in state `dropped` |
| Rules | Re-push when `items.fingerprint != pushed_fingerprint` (Story 5 scenario 4). A re-push adds a new source, and the old one becomes a drop: `dropped` if the API removed it, otherwise `pending_manual_drop` [R13]. Text sources must be 1 MB or smaller [R13]. A larger store file is reported, not truncated |

## Entity: Review proposal

| Aspect | Detail |
|--------|--------|
| Spec ref | Key Entities → Review proposal; FR-028 to FR-030; Story 6 |
| Path | `packs/<topic>.review.yaml` [new] |
| Fields | `topic` · `proposal_id` · `created_at` · `notebook_id` · `free_slots_at_creation` · `entries[] {action, ref, title, reason, detail, candidate}` |
| `action` | `drop` · `repush` · `add` · `replace` (for an unreachable web URL with a suggested substitute) |
| `reason` [new codes] | `changed_since_push` → repush · `deleted_upstream` → drop · `url_unreachable` → drop or replace · `stale_uncited` (`modified` and `last_cited_at` both older than `KB_SYNC_REVIEW_STALE_MONTHS`, default 12) → candidate drop · `near_duplicate` → candidate drop · `new_match` → candidate add · `web_search` (FR-030, only with the opt-in flag) → candidate add |
| `candidate` | true for owner-judgement entries. These are written commented out or flagged `approve: false` [new], so an unedited apply runs only the non-candidate entries plus any the owner has switched on |
| Apply rules | `review <topic> --apply` reads only the file as it stands (Story 6 scenarios 2 and 3). It refuses if `proposal_id` has already been applied or the topic's notebook changed since `created_at`. Drops the API cannot perform → `pending_manual_drop`, listed for manual removal (FR-029). Nothing is dropped without an entry in the applied file |

---

## Entity: Passage and Answer (transient)

These live in memory for one `ask` or `kb_search` call and are never persisted, apart from
`items.last_cited_at`.

| Aspect | Detail |
|--------|--------|
| Spec ref | FR-019 to FR-024; Story 2, Story 3; R1, R4, R6, R7 |
| Passage | `n` (citation number, assigned in rank order after filtering) · `reference_id` and `file_path` (from LightRAG's `references`) · `content` · `score` (reranker score or local 0-3 score; null for `none`) · `item_id` (resolved from `file_path`, see Staged copy) |
| Answer | `text` · `filter` (`reranker`, `local`, `none`) · `answerer` (`claude`, `local`) · `sources[]`, each rendered as `[n] Title (Drive\|AppFlowy\|NotebookLM\|Web\|Research) link` [spec] |
| Rules | Only chunk passages are kept, and entity and relationship blocks are dropped [R1]. Reranker: keep `score ≥ MIN_RERANK_SCORE` (0.6). Local fallback: keep the top `KB_SYNC_LOCAL_TOP_N` (8) with `score ≥ 1` [R6]. Zero surviving passages → the text `not in the knowledge base`, and no call to Claude (FR-022). The answer always states the filter that ran (FR-020). `--local` uses `answerer=local` and makes no outbound call (SC-009) |
| Unresolved passages (open) [new] | A `file_path` with no `__` (a file added by hand under feature 001) or with no matching record has no title, label or link. Proposed: keep the passage and cite it as `[n] <file_path>` with no label or link, and flag it in the output. This is outside the pinned citation format, so **owner decision needed** |
| `kb_get_source` result | `title`, `source`, `url`, `modified`, `locations[]`, and the store-file text (FR-024). It errors if the item is `removed` or has no store file |

---

## Entity: Configuration (additions to `.env`)

`.env` is still the single configuration surface. `.env.example` is updated with it and the
two stay byte-identical. Every comment sits on its own line.

| Key | Default | Source |
|-----|---------|--------|
| `KB_SYNC_DRIVE_FOLDER_IDS` | `1qIMoMk98MsRmIJvLsfqo5Pv7f7aKlKRk` (Leadership) | [spec], comma-separated [new] |
| `KB_SYNC_APPFLOWY_BASE_URL` | Phase 0 check | [R9] |
| `KB_SYNC_APPFLOWY_WORKSPACE_ID` | empty (AppFlowy disabled) | [new] |
| `KB_SYNC_APPFLOWY_NOTE_DATABASES` | empty; comma-separated database IDs stored one file per row | [new], FR-016 |
| `KB_SYNC_RESEARCH_DIR` | empty (disabled); host path mounted read-only | [new], FR-017 |
| `KB_SYNC_NOTEBOOKLM_OUTPUT_PATTERN` | Phase 0 check against real saved outputs | [new], FR-014 |
| `KB_SYNC_CLAUDE_MODEL` | `claude-sonnet-5-5` | [R7] |
| `KB_SYNC_FILTER` | `reranker` (`reranker`, `local`, `none`) | [new], FR-020 |
| `KB_SYNC_LOCAL_TOP_N` | `8` | [R6] |
| `KB_SYNC_REMOVAL_LIMIT` | `10` | [plan], spec Assumptions |
| `KB_SYNC_NOTEBOOK_SOURCE_LIMIT` | `50` | [new], spec Assumptions |
| `KB_SYNC_REVIEW_STALE_MONTHS` | `12` | [new], spec Assumptions |
| `KB_SYNC_NOTEBOOKLM_ENABLED` | `false` | [R13] |
| `RERANK_BINDING` · `RERANK_MODEL` · `RERANK_BINDING_HOST` · `MIN_RERANK_SCORE` | `cohere` · `bge-reranker-v2-m3` · `http://127.0.0.1:${RERANKER_PORT}/v1/rerank` · `0.6` | [spec], [R4]. LightRAG reads these too |
| `RERANKER_PORT` · `RERANKER_IMAGE_TAG` | Phase 0 check | [R5] |

**Validation**: `KB_SYNC_REMOVAL_LIMIT` and `KB_SYNC_NOTEBOOK_SOURCE_LIMIT` are positive
integers. `MIN_RERANK_SCORE` lies in [0, 1]. `KB_SYNC_FILTER` is one of three values. Each
folder ID matches `[A-Za-z0-9_-]+`. A bad value exits 2 and names the key.

## Entity: Secrets

| Aspect | Detail |
|--------|--------|
| Spec ref | FR-004, FR-018; SC-010 |
| Location | `secrets/` at the repo root (gitignored, directory mode `0700`, files `0600`, owned by uid 1000) |
| Files | `google-client.json` (OAuth client, placed by the owner) · `google-token.json` (refresh token, `drive.readonly` [spec]; NotebookLM scope added only when enabled [R13]) · `appflowy.json` (GoTrue refresh token [R9]) · `anthropic-key` (API key [R7]) [new names] |
| Rules | Written only by `kb.sh auth <source>`. Read by kb-sync at startup. Never copied into records, run logs, store files, plans, packs or backups. `kb.sh` refuses to run (exit 3) if the mode is looser than `0600` |

---

## State transitions: Item record

`status` is the spec value. `pending` and `held` are internal. Each arrow commits in its own
SQLite transaction before the next step starts, so an interrupted run resumes without
duplicates [plan, R11].

```text
                 pull: new, disposition=ingest
   (none) ─────────────────────────────────────► stale / pending=ingest
   (none) ── pull: new, skip_media|record_only|duplicate ──► active / pending=none (never staged)

   stale / ingest ── ingest: stage → scan → bind doc_id ──► active / none
   stale / ingest ── ingest: LightRAG reports failed ─────► stale / none, failure set
                                                            (retried only when fingerprint changes)

   active / none ── pull: fingerprint changed ─────────────► stale / replace   (old doc_id kept)
   stale / replace ── ingest (after backup): delete old doc
                      → poll idle → stage → scan → bind ───► active / none
   active / none ── pull: title/url/locations changed only ► active / none     (records updated, no re-ingest)

   active / none ── pull: deleted | trashed | out of allow-list ──► active / remove
   active / remove ── ingest, within limit, after backup ────────► removed / none
                                                                    (doc_id cleared, store file → .removed/)
   active / remove ── over limit, or reason=out_of_allow_list ───► active / remove, held=true
   held=true ── kb.sh ingest --confirm-removals (after backup) ──► removed / none
   active / remove ── pull: item reappears in scope ─────────────► active / none, held=false

   removed ── pull: same (source, source_id) restored upstream ──► stale / ingest
```

**Notes**

- `stale` means the knowledge base does not hold the current content (new, changed or failed).
  `active` means it does, or that the item is intentionally not ingested (`disposition` other
  than `ingest`).
- During `stale/replace`, `ask` may still cite the old version. Answers lag edits until the next
  ingest (SC-001 measures after one pull and ingest).
- A removal that LightRAG answers with `not_allowed`, or that fails after retries on `busy`,
  leaves the item in `active/remove` and writes a removal entry with `outcome=failed`.

## State transitions: other entities

```text
Ingest plan:      built (--plan) ──► applied (--apply, applied_at set)
                  built ──► superseded (a newer --plan overwrites it)

Notebook source:  (none) ── push ──► active
                  active ── repush or approved drop, API removes ──► dropped
                  active ── approved drop, API cannot remove ──► pending_manual_drop
                  pending_manual_drop ── later review sees it gone from the notebook ──► dropped

Review proposal:  written (review) ──► edited by owner (optional) ──► applied (review --apply)
                  written ──► stale (notebook changed since created_at; apply refuses, re-run review)

Source auth:      missing ── kb.sh auth ──► ok ── token refresh fails ──► expired ── kb.sh auth ──► ok
```

---

## Entity: Backup artifact (reshaped from 001)

| Aspect | Change from 001 |
|--------|-----------------|
| Contents | The archive now holds `data/kb-sync/` (records DB, run logs, plan file) next to `data/rag_storage/` (FR-005). `--with-inputs` still adds `data/inputs/`, which includes store files |
| Manifest | Adds `kb_sync_records: true\|false` and `kb_sync_schema_version` [new] |
| Consistency | `backup.sh` stops every service first, and kb-sync is one-shot, so the SQLite file is closed during archiving [R11] |
| Restore | `restore.sh` restores both trees together. The current `data/kb-sync/` moves to `data/kb-sync.pre-restore-<ts>/` alongside `data/rag_storage.pre-restore-<ts>/`, never deleted (edge case "Restore"). An archive without `data/kb-sync/` (made before this feature) restores the KB and warns that records are missing. The next pull rebuilds them, and every item looks new |
| After restore | Records and KB match each other. Store files may be newer than the records, but the next pull compares upstream with records and rewrites any store file whose fingerprint differs |

---

## Amended invariants

These replace feature 001's invariants for the whole repository. Feature 001's
`data-model.md` gets a pointer to this section (plan, Complexity Tracking).

**Invariant 1 (amended)**: no routine command deletes or overwrites anything under
`data/rag_storage/`. The routine set is 001's commands plus `kb.sh auth`, `pull`, `ask`,
`mcp`, `pack`, `notebook push` and `review` (without `--apply`). `kb.sh ingest` and
`kb.sh nightly` may remove knowledge only under Invariant 2.

**Invariant 2 (amended, constitution v1.1.0)**: only two paths can remove KB content:
`restore.sh` (guarded, moves rather than deletes) and `kb-sync ingest --apply` for items with
`pending=remove` or `pending=replace`, through `DELETE /documents/delete_document`. kb-sync's
LightRAG client has no way to call bare `DELETE /documents`.

**Invariant 3 (unchanged)**: `EMBEDDING_DIM` equals the native dimension of `EMBEDDING_MODEL`.

**Invariant 4 (extended)**: no container (`lightrag`, `kb-sync`, `reranker`) has a mount path
to `data/backups/`. `kb-sync` and `reranker` also have no mount of `data/rag_storage/`.

**Invariant 5 (new)**: every removal, including the delete half of a replace, follows a
backup taken earlier in the same `kb.sh` run. The removal entry records that backup (FR-010,
SC-008).

**Invariant 6 (new)**: a run never removes more than `KB_SYNC_REMOVAL_LIMIT` documents. Above
the limit it removes none and holds them all. Removals caused by a change to the allow-list
are always held (FR-011, edge case "Folder removed from config").

**Invariant 7 (new)**: `data/kb-sync/` and `data/rag_storage/` are backed up and restored
together, so records never describe a KB other than the one present (FR-005).

**Invariant 8 (new)**: secrets live only in `secrets/` and never appear in records, logs, store
files, packs, plans, backups or stdout (FR-004).

**Invariant 9 (new)**: kb-sync never reads Drive files outside the configured folder trees
(FR-006). The `drive.readonly` scope is narrowed by code, so a contract test asserts that
out-of-tree changes are dropped before any download.
