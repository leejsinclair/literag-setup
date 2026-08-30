# Architecture

## Component overview

```
        host: EndeavourOS (Arch), x86_64, CPU only
        ┌───────────────────────────────────────────────────────────────┐
        │                                                               │
        │   Docker Engine + Compose v2                                   │
        │   ┌───────────────────────────────────────────┐               │
        │   │ container: literag                        │               │
        │   │   image ghcr.io/hkuds/lightrag:<pinned>   │               │
        │   │   LightRAG server → binds 127.0.0.1:9621  │               │
        │   │     • Web UI        (GET /)               │               │
        │   │     • REST API      (/query, /documents…) │               │
        │   │     • knowledge graph + vector index      │               │
        │   │     • ingestion ledger (doc-status)       │               │
        │   │   network_mode: host  (shares host netns) │               │
        │   └──────┬───────────────────────┬────────────┘               │
        │          │ bind mounts           │ http://localhost:11434     │
        │          ▼                       ▼                            │
        │   ./data/{inputs,rag_storage,   Ollama (host service)         │
        │          prompts}   ./.env       • LLM_MODEL  (query+extract) │
        │   ./data/backups (host only,     • EMBEDDING_MODEL            │
        │          not mounted)            127.0.0.1:11434, unexposed   │
        └───────────────────────────────────────────────────────────────┘
```

- **One container, one Compose project.** No database, no sidecars, no reverse
  proxy (Principle I, IX). LightRAG itself provides the Web UI, API, graph,
  index and document tracking — this repo only configures and operates it.
- **Ollama stays on the host**, unbundled and unmodified — it is intentionally
  shared infrastructure (Principle II).
- **All configuration is `.env`**, mounted at `/app/.env` and also read by
  Compose for `${...}` interpolation. `compose.yaml` has no `environment:` block.

### Docker ↔ Ollama under host networking

`network_mode: host` puts the container in the host's network namespace. Two
consequences:

1. The LightRAG process binds `127.0.0.1:9621` *on the host directly* — there is
   no Docker port-publishing layer, and the service is loopback-only with nothing
   further to configure (see "Exposure model" below).
2. `http://localhost:11434` inside the container **is** the host's Ollama. No
   `extra_hosts`, no `host.docker.internal`, no `OLLAMA_HOST=0.0.0.0` — Ollama
   keeps its default loopback bind and is never exposed. The bridge-network
   alternative (and why it is only a fallback) is in `docs/TROUBLESHOOTING.md`
   and `research.md` Decision 2.

### Data-location map

| What | Host | Container | Notes |
|------|------|-----------|-------|
| Source documents | `data/inputs/` | `/app/data/inputs` | you put files here; `ingest.sh` scans |
| **Knowledge base** | `data/rag_storage/` | `/app/data/rag_storage` | graph + vectors + KV + ledger; the thing backups capture |
| Prompt overrides | `data/prompts/` | `/app/data/prompts` | optional |
| Config | `.env` | `/app/.env` (ro) | gitignored; `.env.example` committed |
| Backups | `data/backups/` | *(not mounted)* | archives + manifests |

<!-- Sections below were filled across implementation phases:
     Exposure model (T020) · Change-detection (T029) · Persistence (T013) ·
     Scale ceiling + pinned image (T031/T033). -->

## Exposure model — loopback only, no authentication

The Web UI and REST API are reachable **only from this machine**. There is no
authentication; the loopback binding is the entire access-control mechanism
(clarification Q4, FR-028, FR-028a).

How it is enforced:

- `compose.yaml` uses `network_mode: host` — the container shares the host's
  network namespace, so there is no port-publishing layer at all (no `ports:`).
- The LightRAG process binds the address in `.env`: `HOST=127.0.0.1`, `PORT=9621`.
  A process bound to `127.0.0.1` accepts connections only from the loopback
  interface; packets arriving on the LAN/Wi-Fi interface to that port are
  refused by the kernel.
- The host's Ollama is reached at `http://localhost:11434` — also loopback, also
  unexposed. No Ollama reconfiguration is needed.

### Verify loopback-only reachability

On the machine (should succeed):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9621/health   # 200
```

From another device on the same network, or on the machine using its LAN IP
(should fail — connection refused / timeout):

```bash
LAN_IP=$(ip -4 addr show scope global | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)
curl -sS --max-time 5 -o /dev/null -w '%{http_code}\n' "http://$LAN_IP:9621/health"
#   → curl: (7) Failed to connect ...   (no HTTP response at all)
```

`scripts/health.sh` warns if `HOST` in `.env` is not `127.0.0.1`, and
`scripts/smoke-test.sh` check **C7** performs the non-loopback probe automatically
when the machine has a second interface.

⚠ Before ever setting `HOST` to `0.0.0.0` or a LAN address you MUST set
`LIGHTRAG_API_KEY` in `.env` first (FR-028a) and revise
`contracts/lightrag-api.md` so every script sends `X-API-Key`.

---

## Change-detection behaviour

LightRAG tracks every document in its ingestion ledger
(`JsonDocStatusStorage`, under `data/rag_storage/`) keyed by **file name +
content hash**. When `./scripts/ingest.sh` triggers `POST /documents/scan`:

| Situation | What happens |
|-----------|--------------|
| New file | enqueued and processed |
| File whose content changed since last ingestion | re-enqueued and reprocessed (the new hash does not match the ledger) |
| File unchanged since its last successful ingestion | **skipped** — not enqueued, regardless of its ledger state (FR-005) |
| File left mid-pipeline by an interrupted run (`parsing`/`analyzing`/`processing`) | reset to `pending` and resumed on the next run, without re-extraction (FR-007) |
| Unsupported extension | never a candidate; counted as "skipped (unsupported)" by `ingest.sh` (FR-004) |

So re-running `ingest.sh` on an unchanged collection enqueues **0** documents
(SC-006) — the summary reads `0 processed / N skipped (unchanged) / ...`.

### Removing a file from `data/inputs/` does NOT remove its knowledge

Deleting a source file after it has been ingested leaves its extracted entities,
relationships and chunks in the knowledge base (spec Assumption). There is no
automatic reconciliation between `data/inputs/` and the knowledge base. Removing
content from the KB is a deliberate, owner-initiated action (the Web UI's
document-delete, or `DELETE /documents/{doc_id}`) — never something a routine
command does.

---

## Persistence & data categories

Everything the knowledge base needs to survive lives in **host bind mounts**
under `data/`, not inside the container filesystem. There are four distinct
categories:

| Category | Host path | Container path | Survives `down` / image swap? | In backups? |
|----------|-----------|----------------|-------------------------------|-------------|
| Source documents | `data/inputs/` | `/app/data/inputs` (`INPUT_DIR`) | yes (bind mount) | optional (`backup.sh --with-inputs`) |
| **LightRAG persistent state — the knowledge base** | `data/rag_storage/` | `/app/data/rag_storage` (`WORKING_DIR`) | **yes (bind mount)** | **yes — this is what backup captures** |
| Prompt overrides | `data/prompts/` | `/app/data/prompts` (`PROMPT_DIR`) | yes (bind mount) | no (reproducible from repo) |
| Backup archives | `data/backups/` | *not mounted* | yes (host-only) | n/a (is the backup) |

`data/rag_storage/` contains, as plain files:

| File(s) | Backend | Holds |
|---------|---------|-------|
| `kv_store_*.json` | `JsonKVStorage` | chunks, LLM-cache (disabled by default), full-doc text |
| doc-status records (JSON) | `JsonDocStatusStorage` | the **ingestion ledger** — per-document status + content hash |
| `graph_chunk_entity_relation.graphml` | `NetworkXStorage` | the knowledge graph (entities + relationships) |
| `vdb_*.json` | `NanoVectorDBStorage` | the retrieval index (embeddings, dimension = `EMBEDDING_DIM`) |

### Why it survives container recreation and image updates

Bind mounts are directories on the host that the container reads and writes
through. `docker compose down`, `docker compose up` on a new image, and
`./scripts/restart.sh --recreate` all destroy and rebuild the **container** — the
host directory is untouched. On the next start LightRAG opens the same files and
the knowledge graph, index and ledger are exactly as they were, with no
re-ingestion (FR-012, SC-004, SC-005).

This is why `stop.sh`, `restart.sh` and `update.sh` never pass `-v` / `--volumes`
to `docker compose down`: that flag would remove named volumes, and while this
project uses bind mounts (not named volumes) the prohibition is absolute so the
scripts can never regress into deleting data.

### Invariant 1 — routine commands never write under `rag_storage/`

`start` · `stop` · `restart` · `health` · `logs` · `ingest` · query all treat
`data/rag_storage/` as append-only or read-only. `ingest.sh` only adds content.
The **only** command that may displace `rag_storage/` is `restore.sh`, and only
behind its `--force` + confirmation guard, and even then it *moves* the old
directory aside (`data/rag_storage.pre-restore-<timestamp>/`) rather than
deleting it (Principle V, FR-014).

### `data/backups/` is never mounted into the container (Invariant 4)

The container has no reason to read or write backup archives, so `compose.yaml`
does not mount `data/backups/`. A container-side bug therefore cannot corrupt a
backup.

---

## Scale ceiling and when to add a database

This deployment is sized for a **personal collection — up to a few hundred
documents**, single writer, no concurrency target (SC-013, clarification Q3).

The file-based backends are the simplest thing that works at that scale:

- `NetworkXStorage` loads the **entire knowledge graph into memory at startup**.
- `NanoVectorDBStorage` keeps vectors in JSON and does a linear scan per query.

Both are fine for hundreds of documents. The **concrete signal** that you have
outgrown them — not before (Principle IX) — is any of:

- container startup takes tens of seconds or more because the `.graphml` load
  dominates;
- query latency is dominated by vector search rather than by the CPU LLM;
- `data/rag_storage/` JSON files reach hundreds of MB and rewrites stutter.

Only then does introducing a graph/vector database (PostgreSQL + pgvector, or
Neo4j) become a justified, discrete change — and it also means re-doing the
backup strategy (a filesystem tar no longer captures a running database
cleanly). Until a real limit is observed, adding one is prohibited complexity.

## Pinned image

`.env` pins `LIGHTRAG_IMAGE_TAG`. `compose.yaml` references
`ghcr.io/hkuds/lightrag:${LIGHTRAG_IMAGE_TAG}` and nothing else changes it except
`scripts/update.sh` (which backs up first).

| Field | Value |
|-------|-------|
| Tag (`.env.example` default) | `v1.5.6` |
| Variant | default (non-`lite`) — bundles spaCy models + the offline legacy PDF/text parser |
| Resolved digest | `sha256:ab23a9c83a735901b18c8960b6b482b602d5b6291abb7e07c5776f7bb2da504e` |
| Registry | GitHub Container Registry (`ghcr.io`), multi-arch, Cosign-signed |

To confirm the digest you actually pulled:

```bash
docker inspect --format '{{index .RepoDigests 0}}' \
  "ghcr.io/hkuds/lightrag:$(grep -E '^LIGHTRAG_IMAGE_TAG=' .env | cut -d= -f2)"
```

`backup.sh` records the tag **and** digest in every backup manifest, so a restore
onto a different image is detectable.
