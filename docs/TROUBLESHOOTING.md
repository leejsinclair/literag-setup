# Troubleshooting

Every script prints errors prefixed `ERROR:` naming the failing component and the
likely cause. Start with `./scripts/health.sh` — it checks LightRAG, Ollama
reachability, both model roles, the embedding dimension, and storage writability,
and names whichever one is broken.

---

## Confirm the service is loopback-only (not exposed)

See `docs/ARCHITECTURE.md` → "Exposure model" for the full explanation and the
verification commands. In short: `curl http://127.0.0.1:9621/health` works on the
machine; the same request to the machine's LAN IP must fail to connect. If the
LAN probe *succeeds*, `HOST` in `.env` is not `127.0.0.1` — fix it, set
`LIGHTRAG_API_KEY` if you truly need exposure, and `./scripts/restart.sh`.

---
## Ollama connectivity

`health.sh` says `[ FAIL ] ollama  cannot reach Ollama at http://localhost:11434 ...`

1. Is Ollama running on the host? `curl -s http://localhost:11434/api/tags`
   should return JSON. If not: `systemctl --user start ollama` (or
   `ollama serve` in a terminal).
2. Is `LLM_BINDING_HOST` / `EMBEDDING_BINDING_HOST` correct in `.env`? With this
   deployment's `network_mode: host` the container shares the host network
   namespace, so `http://localhost:11434` is correct and needs **no** Ollama
   change — Ollama can stay on its default `127.0.0.1:11434` bind.

### Fallback: bridge networking instead of host networking

If you must run the container on a bridge network (e.g. host networking is
disallowed), you lose the direct `localhost` route to Ollama. Then:

- In `compose.yaml`: remove `network_mode: host`, add
  `ports: ["127.0.0.1:${PORT}:9621"]` and
  `extra_hosts: ["host.docker.internal:host-gateway"]`.
- Set `LLM_BINDING_HOST` / `EMBEDDING_BINDING_HOST` to
  `http://host.docker.internal:11434` in `.env`.
- Make the host Ollama listen where the container can reach it — set
  `OLLAMA_HOST=0.0.0.0:11434` (systemd drop-in for the `ollama` service) or at
  least the `docker0` gateway IP. This widens Ollama's exposure, which is why
  host networking is the default (research.md Decision 2).

Keep the published port bound to `127.0.0.1` either way.

---

## Model not installed

`health.sh` says `[ FAIL ] models  not installed: EMBEDDING_MODEL=bge-m3:latest`

```bash
ollama pull qwen2.5:3b-instruct     # LLM_MODEL
ollama pull bge-m3                   # EMBEDDING_MODEL
ollama list                         # confirm
```

The name in `.env` must match what `ollama list` shows (a bare `bge-m3` matches
`bge-m3:latest`).

---

## Embedding dimension mismatch

`health.sh` says `[ FAIL ] embedding-role  DIMENSION MISMATCH: bge-m3:latest returns 1024 but EMBEDDING_DIM=768`

`EMBEDDING_DIM` in `.env` must equal the model's native output dimension
(Invariant 3). Fix the value and restart. If you had **already ingested**
documents with the wrong value, the vector index is inconsistent — see
`docs/MODEL_SELECTION.md` → re-index procedure.

---

## Storage not writable

`health.sh` says `[ FAIL ] storage  cannot write to data/rag_storage/`

The image runs as uid 1000 (`lightrag`). The bind-mounted `data/` directories
must be writable by that uid. If your host user is also uid 1000 (typical
single-user desktop) this just works. Otherwise:

```bash
sudo chown -R 1000:1000 data/rag_storage data/inputs data/prompts
```

---

## Disk fills during ingestion or backup

- **Ingestion**: documents in progress fail with a disk-space error and are
  marked `failed`; already-processed documents stay valid. Free space, then
  re-run `./scripts/ingest.sh` — it retries only the unfinished/failed ones.
- **Backup**: `backup.sh` deletes its partial archive and returns the service to
  its prior state, so a failed backup leaves nothing behind. Free space (or use
  `--output` to point at a larger filesystem) and re-run.

Check free space with `df -h .` before large operations.

---

## Everything is slow and neither CPU nor GPU looks busy

The model is split across CPU and GPU. Check:

```bash
ollama ps        # PROCESSOR column
```

If it shows something like `9%/91% CPU/GPU` (or `100% CPU`), Ollama could not fit
the model fully on the GPU and the split stalls the pipeline. Fixes, in order:

1. Confirm `OLLAMA_LLM_NUM_GPU=99` and `OLLAMA_EMBEDDING_NUM_GPU=99` are in `.env`,
   then `./scripts/restart.sh`.
2. Confirm the tuned Ollama service is active:
   `systemctl --user show ollama -p Environment` should list
   `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`. If not, run
   `./scripts/apply-perf-tuning.sh`.
3. VRAM ceiling — `nvidia-smi` at/near `6144 MiB` with both models loaded. Confirm
   `OLLAMA_NUM_PARALLEL=1` in `~/.config/systemd/user/ollama.service` (it **must**
   be 1 on a 6 GB card — see the next entry), then drop `OLLAMA_LLM_NUM_CTX` /
   `OLLAMA_EMBEDDING_NUM_CTX` in `.env`. Closing GPU-heavy desktop apps (browsers,
   Discord) frees ~1–1.5 GB.
4. Full detail: `docs/MODEL_SELECTION.md` → "Performance tuning".

---

## Ingestion halts: "Embedding func: Worker execution timeout after 60s"

`pipeline_status.latest_message` reads *"Pipeline halted on internal storage error
(… NanoVectorDBStorage[entities]: Embedding func: Worker execution timeout after
60s)"* and the affected documents flip to `failed`.

A single `bge-m3` batch took longer than LightRAG's embedding-worker ceiling (2×
`EMBEDDING_TIMEOUT`). On this 6 GB card the cause is **model thrash**: with
`OLLAMA_NUM_PARALLEL=2` the LLM and the embedding model don't fit together, so
Ollama evicts and reloads one to serve the other on every ingest phase switch, and
a merge-phase embedding batch ends up queued behind a full model reload.

```bash
ollama ps          # during ingest — if only ONE model is ever listed, they're thrashing
```

Fix:

1. `OLLAMA_NUM_PARALLEL=1` in `~/.config/systemd/user/ollama.service`, then
   `systemctl --user daemon-reload && systemctl --user restart ollama`. With it at
   1, `ollama ps` shows **both** models at `100% GPU` (~4.8 GB) and they stay
   resident.
2. In `.env`: `EMBEDDING_BATCH_NUM=10`, `OLLAMA_EMBEDDING_NUM_CTX=2048`,
   `EMBEDDING_TIMEOUT=120` (all already set in the tuned profile).
3. `./scripts/restart.sh --recreate`, then `./scripts/ingest.sh` — the `failed`
   documents are re-enqueued automatically.

---

## A document is stuck in `processing` for many minutes / ingestion times out

Almost always the extraction LLM ran away — a small model looping on LightRAG's
structured-extraction prompt. Confirm with `./scripts/logs.sh`: repeated
extraction activity with no "Completed merging" line, and eventually
`httpx.ReadTimeout` + `Failed to extract document`.

Fixes (see `docs/MODEL_SELECTION.md` for detail):

1. Make sure `OLLAMA_LLM_REPEAT_PENALTY` (`1.15`) and `OLLAMA_LLM_NUM_PREDICT`
   (`3072`) are set in `.env` — the two guards against runaway generation. Without
   them a stuck call runs until `LLM_TIMEOUT`. (Don't push the penalty past ~1.2 —
   qwen starts dropping fields from the extraction tuples.)
2. Lower `OLLAMA_LLM_NUM_PREDICT` to `2048`.
3. Check `ollama ps` shows `100% GPU` (see the entry above).
4. Switch `LLM_MODEL` to `llama3.2:3b`, or to a 7B–8B model for reliability
   (a 7B will spill off a 6 GB card).

After changing `.env`, `./scripts/restart.sh` then re-run `./scripts/ingest.sh`
(the stuck document resumes; nothing already done is reprocessed).

---

## Interrupted ingestion

If the service was stopped or the machine powered off mid-ingest, documents left
in `parsing` / `analyzing` / `processing` are **automatically reset to pending**
the next time the pipeline runs — but starting the server does not itself start a
run. Just re-run:

```bash
./scripts/ingest.sh
```

It resumes the unfinished documents without re-extracting the ones already done
and without corrupting the knowledge base (FR-007).

If `ingest.sh` reports the workspace is *fenced pending recovery* (a rare state
after a hard crash mid-write), read the message it prints — recovery is an
explicit `POST /documents/scan` or, for the blocked case,
`POST /documents/recovery/force_reset` then `POST /documents/scan`. This is an
owner-initiated action, not something a routine script does for you.

---

## Restore attempted onto a live knowledge base

`restore.sh` **exits 4** with
`refusing to overwrite an existing knowledge base ...` when `data/rag_storage/`
is non-empty and you did not pass `--force`. This is deliberate (FR-014). If you
really mean to replace the current KB:

```bash
./scripts/restore.sh <archive> --force
```

It will ask for a `yes` confirmation, then **move** (not delete) the current
`data/rag_storage/` to `data/rag_storage.pre-restore-<timestamp>/` before
extracting. Recover the old state by stopping the service and moving that
directory back.

