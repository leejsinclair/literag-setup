# LiteRAG Setup — Local LightRAG Knowledge Base

A personal, single-user knowledge base built on [LightRAG](https://github.com/HKUDS/LightRAG).
It ingests your local documents (`.txt`, `.md`, text-searchable `.pdf`), builds a knowledge
graph and retrieval index from them, and lets you query that knowledge through the LightRAG
Web UI and REST API. Everything runs locally: LightRAG in a single Docker Compose service,
talking to the host's Ollama for a small language model and embedding model. Runs on CPU
alone; where a GPU is present it is used (see `docs/MODEL_SELECTION.md` → "Performance
tuning"). The Web UI and API are bound to `127.0.0.1` only — there is **no authentication**
and the service must not be exposed off the local machine.

This repository *is* the deployment: `compose.yaml`, `.env.example`, `scripts/`, `docs/`.

## Prerequisites (host)

- Docker Engine + Docker Compose v2 (`docker compose version`), daemon running
- Ollama installed and running on the host — see [Ollama setup](#ollama-setup) below
- `curl`, `jq`, `tar` (`pacman -S jq` on EndeavourOS; `curl`/`tar` are usually present)
- No GPU required. If you have an NVIDIA GPU, run `./scripts/apply-perf-tuning.sh`
  after setup to install the tuned, GPU-accelerated Ollama service.

Full prerequisite table and first-time setup (target: **under 30 minutes**):
[`specs/001-lightrag-knowledge-base/quickstart.md`](specs/001-lightrag-knowledge-base/quickstart.md).

### Ollama setup

LightRAG does not run the models itself — it calls a plain [Ollama](https://ollama.com)
([github.com/ollama/ollama](https://github.com/ollama/ollama)) server on the host over
`http://localhost:11434`. The container uses the host network namespace and reaches it
directly, so **no Ollama-side configuration is needed** for the default setup.

**1. Install Ollama.** Linux one-liner (see the
[Linux install docs](https://github.com/ollama/ollama/blob/main/docs/linux.md) for the
manual / no-root method — this repo's box uses a manual install at `~/.local/bin/ollama`):

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**2. Start the server** and confirm it answers:

```bash
ollama serve            # or: systemctl --user start ollama
curl -s http://localhost:11434/api/tags
```

**3. Pull the two models** referenced by `.env.example`:

| Model | `.env` key | Role | Size | Notes |
|---|---|---|---|---|
| `qwen2.5:3b-instruct` | `LLM_MODEL` | entity/relation extraction + query answering | ~1.9 GB | Any Ollama chat model works; changing it is a `restart.sh`, no re-index. |
| `bge-m3` (`bge-m3:latest`) | `EMBEDDING_MODEL` | text embeddings (1024-dim) | ~1.2 GB | `EMBEDDING_DIM` **must** match the model's native dimension. Changing it invalidates the vector index — full re-ingest required. |

```bash
ollama pull qwen2.5:3b-instruct
ollama pull bge-m3
```

If you edit `LLM_MODEL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` in `.env`, pull the matching
model first — `./scripts/health.sh` fails loudly if a configured model is not installed.
See [`docs/MODEL_SELECTION.md`](docs/MODEL_SELECTION.md) for how to choose alternatives.

**4. (NVIDIA GPU only)** After the LightRAG service is up, run
`./scripts/apply-perf-tuning.sh` to install the tuned systemd **user** service
([`ollama/ollama.service`](ollama/ollama.service)) that forces full GPU offload and keeps
both models resident. The stock `.env` is already tuned for a 6 GB card; adjust it for
other GPUs per `docs/MODEL_SELECTION.md` → "Performance tuning".

## Quickstart

```bash
cp .env.example .env && chmod 0644 .env    # then edit models / image tag to match your Ollama
./scripts/start.sh                         # bring the service up, wait for health
./scripts/health.sh                        # verify LightRAG + Ollama + models + storage

# drop documents into data/inputs/ then:
./scripts/ingest.sh                        # scan + ingest; unchanged files are skipped

# query at http://127.0.0.1:9621  (Web UI)  or  POST http://127.0.0.1:9621/query
```

## Commands

| Command | Purpose |
|---------|---------|
| `./scripts/start.sh [--timeout S]` | Start the service, wait for `/health` |
| `./scripts/stop.sh` | Stop the service (knowledge base retained on disk) |
| `./scripts/restart.sh [--recreate]` | Restart; `--recreate` fully recreates the container |
| `./scripts/health.sh [--json] [--quiet]` | Dependency-verifying health check |
| `./scripts/logs.sh [--tail N] [--no-follow]` | Tail container logs |
| `./scripts/ingest.sh [--wait-timeout S]` | Scan `data/inputs/` and ingest new/changed docs |
| `./scripts/backup.sh [--with-inputs] [--output DIR]` | Verified backup to `data/backups/` |
| `./scripts/restore.sh <archive> [--force] [--assume-yes] [--with-inputs]` | Guarded restore |
| `./scripts/update.sh [--tag TAG]` | Back up, then pull + recreate on a new image |
| `./scripts/smoke-test.sh [--keep]` | End-to-end validation (contract tests C1–C8) |

Uniform exit codes: `0` ok · `1` failed · `2` bad args · `3` unmet prerequisite ·
`4` refused for safety.

## Validate

On the target machine, after `start.sh` + `health.sh`:

```bash
./scripts/smoke-test.sh
```

Runs start → Web UI → API → ingest (`.md` + `.pdf`) → query → survive container
recreation → loopback-only → backup/restore, and prints a PASS/FAIL table. It
backs up first and restores your pre-test state on exit.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components, Docker ↔ Ollama, where data lives, exposure model, scale ceiling, pinned image digest
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — start/stop/ingest/query/backup/restore/update runbooks, recovery, re-index
- [`docs/MODEL_SELECTION.md`](docs/MODEL_SELECTION.md) — chosen models, **performance tuning** (GPU offload, query-latency and ingest knobs), changing the query LLM vs the embedding model, the `EMBEDDING_DIM` gotcha
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — Ollama connectivity, model-not-installed, dimension mismatch, disk full, interrupted ingestion, restore-onto-live-KB

## Design documents

The constitution, specification, plan, research, data model, contracts, and task
breakdown live under
[`specs/001-lightrag-knowledge-base/`](specs/001-lightrag-knowledge-base/) and
`.specify/memory/constitution.md`.

## What this deliberately does not do

No authentication · no public exposure · no background file-watcher or
ingest-on-startup · no external database or sidecar services · no scheduled
backups/ingestion · no automatic KB deletion when a file leaves `data/inputs/` ·
no Claude Code / MCP integration (the REST API is preserved as the seam).

## Contributing & license

Use it, fork it, adapt it — this repo is MIT-licensed ([`LICENSE.md`](LICENSE.md)).
Contributions are welcome within the minimal-by-design scope; see
[`CONTRIBUTING.md`](CONTRIBUTING.md).
