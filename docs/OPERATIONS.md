# Operations

Everyday operation of the LiteRAG knowledge base. Every command lives in
`scripts/` and reads its configuration from `.env` (see `.env.example`). Run them
from anywhere — each resolves the repo root itself.

Exit codes are uniform across all scripts: `0` success · `1` operation failed ·
`2` bad arguments · `3` unmet prerequisite (missing tool, no `.env`, Docker
daemon down) · `4` refused for safety.

---

## Start the service

```bash
./scripts/start.sh
```

Runs `docker compose up -d` and polls `GET /health` until it returns 200 (up to
`HEALTH_TIMEOUT_SECONDS`, default raised to 180s here for CPU-only first starts).
On success it prints the Web UI URL (`http://127.0.0.1:9621`). On timeout it
prints the last 40 log lines and tells you to run `./scripts/health.sh`.

`start.sh` is idempotent — running it while the service is already up just
re-checks health.

### The Ollama service

LightRAG talks to a **host Ollama**, run as a tuned systemd *user* service
(`ollama/ollama.service` in this repo, installed to
`~/.config/systemd/user/ollama.service`). It is GPU-accelerated and configured for
this box — see `docs/MODEL_SELECTION.md` → "Performance tuning".

```bash
./scripts/apply-perf-tuning.sh          # first-time install / re-apply the tuned profile
systemctl --user status ollama          # is it up?
systemctl --user restart ollama         # after editing the unit
systemctl --user show ollama -p Environment   # confirm the tuning vars are live
ollama ps                               # both models should read "100% GPU"
```

After editing `~/.config/systemd/user/ollama.service`, run
`systemctl --user daemon-reload && systemctl --user restart ollama`.

The service binds `127.0.0.1` only. It is **not** reachable from any other
machine and has **no authentication** — that loopback binding is the sole access
control. Do not change `HOST` in `.env` without first reading
`docs/ARCHITECTURE.md` → exposure model.

---

## Add documents & ingest

1. Copy or move files into `data/inputs/`. Supported types: `.txt`, `.md`, and
   **text-searchable** `.pdf`. Scanned/image-only PDFs are not supported (no OCR)
   and will be reported as failed. Other extensions are skipped and reported.

2. Run the ingestion command:

   ```bash
   ./scripts/ingest.sh
   ```

   It triggers `POST /documents/scan`, then waits until every enqueued document
   is `processed` or `failed`, and prints a summary:

   ```
   3 processed / 0 skipped (unchanged) / 0 failed
   ```

   Documents that are unchanged since their last successful ingestion are **not**
   reprocessed — re-running `ingest.sh` on an unchanged collection enqueues 0
   documents. If any document fails, `ingest.sh` exits non-zero and points you at
   the per-document detail; the documents that succeeded in the same run stay
   valid (the batch is never rolled back).

### Ad-hoc upload via the Web UI

The Web UI's **Documents** panel has an upload control for one-off files. It goes
through the same pipeline as `ingest.sh`.

### There is no automatic ingestion

- **No file-watcher.** Dropping a file in `data/inputs/` does nothing until you
  run `ingest.sh` (or upload via the Web UI).
- **No scan on startup.** Starting or restarting the service never ingests
  anything. Interrupted documents from a previous run are reset and picked up on
  the *next* `ingest.sh`, not at boot.

---

## Query in the Web UI

Open `http://127.0.0.1:9621` and use the query panel. Answers are generated only
from ingested content, and each answer lists the **source references** (the
documents / chunks that informed it). A question with no supporting content
returns an explicit "no information found"-style response rather than a
fabricated answer.

The default retrieval mode is `hybrid` (graph + vector). The other modes
(`naive`, `local`, `global`, `mix`) are selectable in the panel.

### Query speed

The answer LLM must read the entire assembled retrieval context before it writes
a word, and this GPU processes prompt tokens at ~300/s, so context size drives
latency. The shipped `.env` already trims the context budget (`MAX_TOTAL_TOKENS`
and friends — see `docs/MODEL_SELECTION.md` → "Performance tuning"). Beyond that:

- **Use a lighter mode for simple questions.** `local` (entity-centric) and
  `naive` (pure vector) skip most of the graph work `hybrid`/`mix` do:

  ```bash
  curl -s -X POST http://127.0.0.1:9621/query \
    -H 'Content-Type: application/json' \
    -d '{"query":"<your question>","mode":"local"}' | jq '.response_time, .response'
  ```

- **Repeated / rephrased questions are near-instant** — `ENABLE_LLM_CACHE=true`
  serves them from cache.
- `ollama ps` must show `100% GPU` for both models. A CPU/GPU split roughly
  triples query time — see `docs/TROUBLESHOOTING.md` → "Everything is slow".

---

## Recover after container loss

The knowledge base is in host bind mounts, so losing the container is routine,
not a disaster.

### Container deleted / corrupted (checkout intact)

```bash
./scripts/start.sh
```

`docker compose up -d` recreates the container against the existing
`data/rag_storage/`. Nothing is re-ingested. Confirm with `./scripts/health.sh`
and one query.

### Whole checkout lost (disk failure, etc.)

You need: this repository, your `.env` values, and your latest backup archive.

```bash
git clone <this-repo> literag-setup && cd literag-setup
cp .env.example .env
$EDITOR .env                     # restore LIGHTRAG_IMAGE_TAG, model names, EMBEDDING_DIM
./scripts/restore.sh /path/to/kb-<timestamp>.tar.gz    # see "Restore" below
./scripts/start.sh
./scripts/health.sh
```

If you also kept a `--with-inputs` backup, pass `--with-inputs` to `restore.sh`
to bring `data/inputs/` back as well; otherwise re-populate `data/inputs/` from
wherever you keep your source documents (the restored knowledge base is already
complete and queryable without them).

---

## Back up, restore, update

### Back up

```bash
./scripts/backup.sh                 # captures data/rag_storage/
./scripts/backup.sh --with-inputs   # also captures data/inputs/
./scripts/backup.sh --output /mnt/external/kb-backups
```

The service is stopped for a consistent snapshot, then returned to whatever state
it was in. The command writes `data/backups/kb-<UTC>.tar.gz` plus a sidecar
`kb-<UTC>.manifest` (image tag + digest, LightRAG version, `EMBEDDING_MODEL`,
`EMBEDDING_DIM`, archive SHA-256), verifies the archive, and prints its absolute
path on stdout. If any step fails the partial archive is removed and the service
is put back as it was.

Backups deliberately do **not** include the Docker image, the Python environment,
or Ollama model weights — all of those are reconstructible from this repo plus
`ollama pull`.

### Restore

```bash
./scripts/restore.sh data/backups/kb-<UTC>.tar.gz
```

Guard sequence:

1. The archive is validated (readable gzip tar, contains `data/rag_storage/`).
2. The sidecar manifest's `EMBEDDING_MODEL` / `EMBEDDING_DIM` are compared to
   `.env`; a mismatch prints a prominent warning and requires confirmation. Image
   tag / version skew is warned about but does not block (an older backup onto a
   newer image is allowed).
3. If `data/rag_storage/` is **non-empty**, `restore.sh` **exits 4** unless you
   pass `--force`. With `--force` it asks for an interactive `yes/no`
   confirmation (skippable only with `--assume-yes` on non-interactive stdin).
4. The current `data/rag_storage/` is **moved** to
   `data/rag_storage.pre-restore-<UTC>/` — never deleted — then the archive is
   extracted, the service started, and health checked. On failure it rolls back
   by moving the displaced directory into place. The preserved directory's path
   is printed; delete it yourself once you have confirmed the restore.

Add `--with-inputs` only if the archive was made with `--with-inputs`.

### Update LightRAG

```bash
./scripts/update.sh                 # re-pull the tag in .env (refresh its digest)
./scripts/update.sh --tag v1.5.7    # move to a new tag
```

`update.sh` **always runs `backup.sh` first** and aborts if the backup fails —
so a backup exists before every image-tag change (FR-018). It then pulls, recreates
the container, and health-checks. Only on a healthy result is `.env`'s
`LIGHTRAG_IMAGE_TAG` rewritten (when `--tag` was given). If the new image is
unhealthy, `.env` is left untouched and rollback steps are printed (including
`restore.sh` against the backup it just made).

Re-run the research in `specs/001-lightrag-knowledge-base/research.md` before
moving to a materially newer LightRAG version — env-var names and endpoints can
change between releases.

---

## Query via the API

The same knowledge base is reachable over the loopback REST API — this is the
integration seam for future tooling (MCP, Claude Code, local scripts). See
`specs/001-lightrag-knowledge-base/contracts/lightrag-api.md` for the full
contract. **Do not build those integrations now** (Principle X); just use the API
as-is.

```bash
curl -s -X POST http://127.0.0.1:9621/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"When was the Verdant Sluicegate Protocol ratified?","mode":"hybrid"}' | jq
```

Request fields this deployment relies on:

| Field | Meaning |
|-------|---------|
| `query` | the question (required) |
| `mode` | `naive` \| `local` \| `global` \| `hybrid` \| `mix` (default `mix`) |
| `only_need_context` | `true` returns retrieved context without generating an answer |

Response shape:

```json
{
  "response": "The Verdant Sluicegate Protocol was ratified on 3 March 1987 ...",
  "references": [
    { "reference_id": "1", "file_path": "sample.md" }
  ],
  "response_time": 4.1
}
```

`references` lists the source documents that informed the answer (FR-010). A
question with no supporting content returns a `response` that says so rather than
a fabricated answer (FR-011).

`GET http://127.0.0.1:9621/docs` is the interactive API reference (Swagger UI).

---

## Re-index after an embedding-model change

Changing `EMBEDDING_MODEL` (and its `EMBEDDING_DIM`) invalidates the stored vector
index — the old vectors were produced by the old model and are not comparable to
new query embeddings (FR-025). `health.sh` will report
`[ FAIL ] index-freshness ... RE-INDEX REQUIRED` until you do this.

1. Set both keys in `.env` and pull the model:

   ```bash
   $EDITOR .env           # EMBEDDING_MODEL=<new>  and  EMBEDDING_DIM=<native dim>
   ollama pull <new>
   ./scripts/restart.sh
   ```

2. Re-run ingestion against the **existing** `data/inputs/`:

   ```bash
   ./scripts/ingest.sh
   ```

   The source documents are reused (you do not re-collect them). LightRAG
   re-embeds the content; the superseded vector index is replaced. The knowledge
   graph and ingestion ledger are not lost.

3. Confirm:

   ```bash
   ./scripts/health.sh    # index-freshness back to OK
   ```

If you keep the source documents elsewhere and `data/inputs/` is empty, copy them
back in first — re-indexing needs the original files.

---
