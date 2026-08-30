# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repo **is** a deployment, not an application. It stands up the official
[LightRAG](https://github.com/HKUDS/LightRAG) server as a single Docker Compose service on
one machine (the owner's EndeavourOS desktop), talking to the host's Ollama for the LLM and
embedding model. There is no source tree of our own — only `compose.yaml`, `.env` config,
`bash` operational scripts, docs, and Spec Kit design artifacts.

The knowledge base itself (graph, vectors, KV store, ingestion ledger) is plain files under
`data/rag_storage/`, using LightRAG's default file-based backends. No external database, no
sidecar services, no auth — `HOST=127.0.0.1` loopback binding is the **only** access control.

## Commands

All operations are `scripts/*.sh` wrappers over `docker compose`. Uniform exit codes:
`0` ok · `1` failed · `2` bad args · `3` unmet prerequisite · `4` refused for safety.

| Command | Purpose |
|---|---|
| `./scripts/start.sh [--timeout S]` | `compose up -d`, wait for `/health` |
| `./scripts/stop.sh` | `compose down` (KB retained on disk) |
| `./scripts/restart.sh [--recreate]` | restart; `--recreate` = `down` + `up -d` |
| `./scripts/health.sh [--json] [--quiet]` | dependency-verifying check (see below) |
| `./scripts/logs.sh [--tail N] [--no-follow]` | tail container logs |
| `./scripts/ingest.sh [--wait-timeout S]` | `POST /documents/scan` on `data/inputs/`, poll to terminal state |
| `./scripts/backup.sh [--with-inputs] [--output DIR]` | stop → tar `data/rag_storage/` → manifest → verify → restore prior state |
| `./scripts/restore.sh <archive> [--force] [--assume-yes] [--with-inputs]` | guarded restore; never deletes — moves current KB to `data/rag_storage.pre-restore-<ts>/` |
| `./scripts/update.sh [--tag TAG]` | backup → `compose pull` → recreate → health; rewrites `.env` tag only after healthy |
| `./scripts/smoke-test.sh [--keep]` | end-to-end contract tests C1–C8; backs up and restores your state on exit |
| `./scripts/apply-perf-tuning.sh` | one-shot GPU migration: installs `ollama/ollama.service`, moves off the terminal `ollama serve`, recreates the container (no sudo) |

**Testing:** there is no unit-test runner for normal work — `scripts/smoke-test.sh` is the
end-to-end validation and must be run on the target machine (it uses the real Ollama model,
takes minutes, starts from an empty KB). It relies on distinctive facts baked into
`tests/fixtures/sample.md` and `sample.pdf` (regenerate the PDF with
`python3 tests/fixtures/make_sample_pdf.py`). `tests/unit/common.bats` covers `common.sh`
and needs `bats` (optional, nice-to-have).

There is **no `scripts/query.sh`** — queries go through the Web UI at `http://127.0.0.1:9621`
or `POST /query`. For fast factual queries use `"mode":"local"` or `"naive"` (much cheaper
than the `hybrid`/`mix` defaults).

## Architecture: the load-bearing facts

**`.env` is the single authoritative configuration surface.** It is (1) read by Compose for
`${...}` interpolation and (2) bind-mounted read-only at `/app/.env` and loaded by the
LightRAG server directly. `compose.yaml` has **no `environment:` block** — never re-declare
an `.env` key there; a value in two places drifts. `.env` is gitignored; `.env.example` is
the committed template and the two are kept **byte-identical** (edit both, then verify with
`diff`).

**Two `.env` parsers, one gotcha.** LightRAG loads `.env` with python-dotenv, which strips
inline `# comments`. `scripts/lib/common.sh` `load_env()` does **not**. So every comment in
`.env` must be on its own line — never `KEY=value  # note`.

**Host networking.** `network_mode: host`, no `ports:` block. The LightRAG process binds
`${HOST}:${PORT}` itself, and reaches the host Ollama at `http://localhost:11434` with no
Ollama-side change needed. Moving `HOST` off `127.0.0.1` exposes an **unauthenticated**
server — `LIGHTRAG_API_KEY` must be set first (`health.sh` warns on this).

**`common.sh` is sourced, never executed.** Every script does
`source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"`, which runs `set -euo pipefail`,
resolves `REPO_ROOT`, loads `.env`, checks for `docker`/`curl`/`jq`/`tar`, and provides
`die`, `log`/`warn`/`err`, the `compose` wrapper, `service_running`, `wait_for_health`,
`http_get`. All Docker access goes through the `compose()` wrapper — no bare
`docker run/rm/stop <id>` anywhere.

**`health.sh` verifies dependencies actually work**, not just "process up": `/health` 200,
Ollama `/api/tags`, both models present, a real `/api/generate` + `/api/embed` round-trip,
embedding vector length `== EMBEDDING_DIM`, `data/rag_storage/` writable, and index-stamp
drift (below).

**Re-index detection.** `ingest.sh` writes `data/rag_storage/.literag-index.json` stamping
`EMBEDDING_MODEL` + `EMBEDDING_DIM`. `health.sh` and `restore.sh` compare it to current
`.env` and demand a re-index if it drifts. Changing the **embedding** model/dim invalidates
the vector index (wipe `data/rag_storage/`, re-ingest). Changing the **query** LLM is a
restart only — no re-index.

**Data invariants (do not violate):**
1. No routine command (`start/stop/restart/health/logs/ingest`) ever deletes or overwrites
   anything under `data/rag_storage/`.
2. Only `restore.sh` (guarded, needs `--force` + confirmation) can displace KB content —
   and it *moves* rather than deletes.
3. `EMBEDDING_DIM` always equals the model's native dimension.
4. The container never has a mount path to `data/backups/`.

**No ingest-on-startup.** LightRAG v1.5.6 does not scan on boot — ingestion happens only via
`POST /documents/scan` (`ingest.sh`) or a Web UI upload. No file-watcher.

## Performance profile (revised 2026-08-30)

The spec originally said "CPU only". It was then found the box has an **NVIDIA GTX 1660 Ti
(6 GB)** and that Ollama's default partial CPU/GPU split of `qwen2.5:3b` was stalling both
ingest and query. The stack is now GPU-tuned via `.env` (`OLLAMA_LLM_NUM_GPU=99` forces full
offload; trimmed query context `MAX_TOTAL_TOKENS=10000`; aggressive ingest `MAX_GLEANING=0`,
`CHUNK_SIZE=2000`) plus a tuned **systemd user** service `ollama/ollama.service`, applied by
`scripts/apply-perf-tuning.sh`. No topology or storage change; the CPU-only profile is
restorable from git history. Details: `docs/MODEL_SELECTION.md` → "Performance tuning" and
`specs/001-lightrag-knowledge-base/research.md` → "Post-implementation revision".

`OLLAMA_LLM_NUM_GPU` / `OLLAMA_LLM_REPEAT_PENALTY` / etc. are forwarded from `.env` by
LightRAG v1.5.6 (`lightrag/llm/binding_options.py`). Verify GPU placement with `ollama ps`
(should read `100% GPU`); watch `nvidia-smi` stays `< 6144 MiB` (drop `OLLAMA_NUM_PARALLEL`
to 1 in the service file if it spills).

## Working in this repo

- **Spec-driven.** Design lives in `specs/001-lightrag-knowledge-base/` (`spec.md`,
  `plan.md`, `research.md`, `data-model.md`, `contracts/`, `tasks.md`) and the project
  constitution at `.specify/memory/constitution.md`. The constitution has ten binding
  principles — the operative ones here: simplicity over infrastructure, local-first, no
  unjustified components, replaceable models via config, persistent data is sacred,
  evidence over assumption (cite official LightRAG docs — don't invent env vars/endpoints).
  Any change that adds a service, volume, dependency, or network exposure must state which
  principle permits it.
- **Match the existing script idiom:** `set -euo pipefail`, a header comment citing the
  contract + relevant FR/Invariant, `usage()` to stderr, uniform exit codes via `die`,
  progress/errors to stderr and results to stdout, `jq` for all JSON.
- The image is **pinned** (`LIGHTRAG_IMAGE_TAG=v1.5.6`, digest recorded in `.env` and
  `docs/ARCHITECTURE.md`). Only `scripts/update.sh` changes it. Do **not** use a `-lite`
  image tag (needs external MinerU/Docling).
- Ollama is a manual install at `~/.local/bin/ollama` (no package), run as a systemd user
  service after `apply-perf-tuning.sh`; `loginctl` linger is enabled for the user.
- `.claude/skills/` holds Spec Kit skills (`speckit-plan`, `speckit-tasks`, etc.) —
  invoked via `/speckit-*` for design-artifact work.
