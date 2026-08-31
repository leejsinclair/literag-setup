# Phase 0 Research: LightRAG Local Knowledge Base

**Date**: 2026-08-30
**Feature**: [spec.md](./spec.md)
**Constitution basis**: Principle VIII (Evidence Over Assumption) — every configuration
option, endpoint, and image reference below is traceable to current official LightRAG
sources listed under **Sources**.

All version-specific facts were checked against the HKUDS/LightRAG `main` branch on
2026-08-30. The plan pins an explicit image tag in `.env` so a later upstream change
cannot silently alter the deployment; re-run this research before bumping the tag.

---

## Decision 1 — Deployment: official image, no custom build

**Decision**: Deploy the official published image `ghcr.io/hkuds/lightrag:<pinned-tag>`
via a single Docker Compose project. Do not build a custom LightRAG image.

**Rationale**:
- HKUDS publishes official multi-arch images to GHCR, signed with Sigstore Cosign via
  GitHub OIDC. A custom image would add a build/maintenance surface for no benefit
  (Principle I, IX).
- The repo ships a reference `docker-compose.yml`; we adapt it rather than copy it, to
  apply the loopback-binding and host-Ollama constraints.

**Image variant**: use the default (`latest`-family) tag, **not** `-lite`.
- The default image bakes in the spaCy runtime **and** the `en_core_web_sm` /
  `zh_core_web_sm` 3.8.0 models; the `-lite` image omits the models.
- The default image carries the "legacy" extraction dependencies needed for offline
  PDF and plain-text parsing (see Decision 5). `-lite` is intended for setups that push
  parsing to external services, which we explicitly avoid.

**Pinning**: `.env` sets `LIGHTRAG_IMAGE_TAG` (e.g. a dated or semver tag). `compose.yaml`
references `ghcr.io/hkuds/lightrag:${LIGHTRAG_IMAGE_TAG}`. `scripts/update.sh` is the only
path that changes it, and it backs up first (Principle V).

**Alternatives considered**:
- *Build from source Dockerfile* — rejected: maintenance burden, slower updates, no
  signature chain.
- *`-lite` image* — rejected: would require MinerU/Docling sidecar services for PDF,
  violating Principles I and IX and the spec's "no unnecessary supporting services".
- *pip install on host* — rejected outright by Principle II and the feature's explicit
  constraint.

---

## Decision 2 — Container networking: host network namespace

**Decision**: Run the LightRAG container with `network_mode: host`. The LightRAG process
binds `HOST=127.0.0.1`, `PORT=9621` (both from `.env`). No published ports, no bridge.

**Rationale**:
- **Loopback-only exposure (FR-028, FR-028a, Principle III)**: with host networking the
  app itself binds `127.0.0.1:9621`, so the Web UI and API are unreachable from any other
  host. This is stricter than the constitution's "publish to `127.0.0.1`" rule and is
  recorded here as the deliberate documented networking decision the constitution
  requires.
- **Host Ollama reachability (Linux)**: the container reaches Ollama at
  `http://localhost:11434` with **no change to the host Ollama service** — Ollama can stay
  on its default `127.0.0.1:11434` bind. This keeps Ollama unexposed too.
- **Fewer moving parts (Principle I)**: removes the bridge network, the
  `extra_hosts: host-gateway` mapping, and the "publish address vs. container bind
  address" split that the stock compose file juggles with a single overloaded `HOST`
  variable.

**Linux-specific note**: `host.docker.internal` is a Docker Desktop convenience. On
native Linux + Docker Engine it only works when `extra_hosts: "host.docker.internal:host-gateway"`
is set, and even then it resolves to the host's bridge gateway IP — which cannot reach a
service bound to the host's `127.0.0.1`. Host networking sidesteps this entirely.

**Alternatives considered**:
- *Bridge network + `extra_hosts: host-gateway` + set host `OLLAMA_HOST=0.0.0.0:11434`
  via a systemd drop-in* — documented in `docs/TROUBLESHOOTING.md` as the fallback for
  environments where host networking is undesirable. Rejected as the default because it
  forces Ollama to listen on all interfaces (or at least the docker0 IP), widening
  exposure for a single-user box, and adds a host-service edit to the reproduction steps.
- *Put Ollama in the compose project* — rejected: Principle II names Ollama as
  intentionally-shared host infrastructure; the feature forbids bundling it.
- *`network_mode: "host"` with app bound `0.0.0.0`* — rejected: unnecessary exposure; the
  bind must be `127.0.0.1`.

**Consequence for `.env`**: a single `HOST=127.0.0.1` value is correct for both the app
bind and (trivially) the absence of port publishing. `.env.example` documents that
changing `HOST` away from `127.0.0.1` widens exposure and must not be done without first
adding authentication (FR-028a).

---

## Decision 3 — LLM & embedding wiring to host Ollama

**Decision**: Configure both bindings for Ollama in `.env`:

| Variable | Value | Notes |
|----------|-------|-------|
| `LLM_BINDING` | `ollama` | |
| `LLM_BINDING_HOST` | `http://localhost:11434` | host Ollama via host networking |
| `LLM_MODEL` | `${LLM_MODEL}` from `.env` (default `qwen2.5:3b-instruct`) | see Decision 4 |
| `OLLAMA_LLM_NUM_CTX` | `32768` | LightRAG requires ≥ 32k context for graph extraction |
| `LLM_TIMEOUT` | `600` | CPU inference is slow; default 240 s is too tight |
| `MAX_ASYNC_LLM` | `2` | limit concurrent CPU inference |
| `MAX_PARALLEL_INSERT` | `1` | one document through the pipeline at a time on CPU |
| `EMBEDDING_BINDING` | `ollama` | |
| `EMBEDDING_BINDING_HOST` | `http://localhost:11434` | |
| `EMBEDDING_MODEL` | `${EMBEDDING_MODEL}` (default `bge-m3:latest`) | |
| `EMBEDDING_DIM` | `1024` | must match the chosen embedding model exactly |
| `OLLAMA_EMBEDDING_NUM_CTX` | `8192` | |

**Rationale**:
- LightRAG's Ollama path needs a large context window for entity/relation extraction;
  upstream env docs set `OLLAMA_LLM_NUM_CTX=32768`. If the model's own default is smaller,
  extraction silently truncates. Setting it here (or `OLLAMA_CONTEXT_LENGTH` on the host
  Ollama service) is mandatory.
- `EMBEDDING_DIM` must equal the embedding model's native dimension or vector storage
  breaks. `bge-m3` = 1024. This value is **model-locked**: changing `EMBEDDING_MODEL`
  generally changes `EMBEDDING_DIM` and invalidates the vector index (FR-025, User
  Story 6, scenario 2).
- All of the above live in `.env` (Principle IV, FR-024). Nothing model-specific is baked
  into `compose.yaml` or the scripts.

**Role verification (FR — model-role check)**: `scripts/health.sh` must confirm, against
the *running* LightRAG version, that:
1. `GET http://localhost:11434/api/tags` lists `LLM_MODEL` and `EMBEDDING_MODEL`.
2. A minimal `POST /api/generate` and `POST /api/embed` against Ollama succeed.
3. `GET http://localhost:9621/health` reports the LLM and embedding bindings as
   configured and reachable.

**Alternatives considered**:
- *Set `OLLAMA_CONTEXT_LENGTH=32768` on the host Ollama systemd unit instead* —
  acceptable and documented in `MODEL_SELECTION.md`, but per-request `OLLAMA_LLM_NUM_CTX`
  keeps the requirement inside the reproducible repo config rather than in host state.
- *OpenAI-compatible generic binding* — unnecessary; the native `ollama` binding is
  first-class.

---

## Decision 4 — Conservative CPU model selection

**Decision**: Default `.env.example` values, all overridable:
- `LLM_MODEL=qwen2.5:3b-instruct` (alt: `llama3.2:3b`)
- `EMBEDDING_MODEL=bge-m3:latest` with `EMBEDDING_DIM=1024`
  (alt: `nomic-embed-text` with `EMBEDDING_DIM=768`)

**Rationale**:
- 3B-class instruct models run on CPU with acceptable latency for a single user and are
  competent enough for LightRAG's structured extraction prompts.
- `bge-m3` is a strong multilingual embedding model that is still small; `nomic-embed-text`
  is lighter if RAM is tight.
- The feature explicitly forbids optimising around large models (FR-026, SC-012).
- `docs/MODEL_SELECTION.md` will record the tested combination, how to change it, the
  `EMBEDDING_DIM` gotcha, and the re-index requirement when the embedding model changes.

**Alternatives considered**: 7B+ LLMs (too slow on CPU for interactive use);
GPU-accelerated embeddings (no GPU present).

---

## Decision 5 — Document formats: native + legacy parsers, no external services

**Decision**: Rely on the image's built-in parsing for the launch formats:
- **Markdown (`.md`)** and **plain text (`.txt`)** — handled directly / via the legacy
  text path.
- **PDF (`.pdf`)** — handled by the legacy extraction path bundled in the default image
  (PyPDF-family). Text-searchable PDFs only; scanned/image PDFs are out of scope (no OCR).

Do **not** deploy MinerU or Docling sidecar services.

**Rationale**:
- The default image includes the legacy extractor covering PDF and Office/text formats
  offline. Docling and MinerU are explicitly documented by upstream as unusable offline
  and are heavyweight — both disqualified by Principles I/III/IX and the spec's
  "no unnecessary supporting services".
- `LIGHTRAG_PARSER` routing is left at its default (native for `docx`/`md`/`textpack`,
  legacy fallback for the rest).

**Validation hook**: the smoke test (FR-031) ingests one `.md` **and** one `.pdf` so the
PDF path is proven on the target machine, not assumed.

**Alternatives considered**: MinerU/Docling (rejected, above); restricting launch scope to
text+markdown only (rejected — the clarification session fixed PDF as in-scope).

---

## Decision 6 — Storage: default file-based backends only

**Decision**: Keep LightRAG's default storage backends. Set them explicitly in `.env` so
the choice is visible and pinned:

| Concern | Backend | On-disk location (under `/app/data/rag_storage`) |
|---------|---------|--------------------------------------------------|
| `LIGHTRAG_KV_STORAGE` | `JsonKVStorage` | `kv_store_*.json` |
| `LIGHTRAG_DOC_STATUS_STORAGE` | `JsonDocStatusStorage` | `doc_status` records (JSON) |
| `LIGHTRAG_GRAPH_STORAGE` | `NetworkXStorage` | `graph_chunk_entity_relation.graphml` |
| `LIGHTRAG_VECTOR_STORAGE` | `NanoVectorDBStorage` | `vdb_*.json` |

**Rationale**:
- The collection is personal-scale — up to a few hundred documents, single writer
  (clarification Q3). File-based storage is the simplest supported configuration and needs
  zero supporting services (Principle I, IX; spec "prefer the simplest supported
  persistent storage").
- Everything persists as plain files under one directory, which makes the backup a
  single-directory archive (Decision 7).

**Explicitly rejected**: PostgreSQL, Neo4j, Redis, Milvus, Qdrant, MongoDB, OpenSearch,
Memgraph. None is required for the selected configuration. Introducing one later is a
discrete, spec-driven change (Principle IX), and would require re-visiting the backup
strategy.

**Scale ceiling / trigger to revisit** (for `docs/ARCHITECTURE.md`): if ingestion or query
latency degrades badly past several hundred documents, or `NetworkXStorage` load time
becomes a startup bottleneck, that is the demonstrated need to consider a graph/vector DB
— not before (SC-013).

---

## Decision 7 — Data layout & persistence boundaries

**Decision**: Four clearly separated categories, three of them host bind mounts:

| Category | Host path | Container path | In VCS? | Backed up? |
|----------|-----------|----------------|---------|------------|
| Source documents | `./data/inputs/` | `/app/data/inputs` (`INPUT_DIR`) | no (gitignored) | optional (`--with-inputs`) |
| LightRAG persistent state | `./data/rag_storage/` | `/app/data/rag_storage` (`WORKING_DIR`) | no | **yes — this is the KB** |
| Prompt overrides (config) | `./data/prompts/` | `/app/data/prompts` (`PROMPT_DIR`) | dir tracked, contents optional | no (reproducible from repo) |
| Application configuration | `./.env` | `/app/.env` | **no** — `.env.example` is committed | no (secrets/host-specific) |
| Backups | `./data/backups/` | *not mounted* | no | n/a (is the backup) |
| Temp / cache | container-internal (tiktoken cache dir set under `rag_storage` or left ephemeral) | — | no | no |

**Rationale**:
- Bind mounts (not named volumes) so the owner can see, archive, and inspect KB state with
  ordinary file tools — aligns with "easy recovery" and Operational Clarity.
- `data/rag_storage` surviving `docker compose down` and image replacement satisfies
  FR-012, User Story 2, SC-004, SC-005, and Principle V. Bind mounts are inherently
  external to the container filesystem.
- Backups directory is deliberately **not** mounted into the container — the container has
  no reason to read or write backups, and keeping it out prevents a container-side bug
  from touching them.
- `.env` is mounted read-only-ish; must be readable by uid 1000 (the image's `lightrag`
  user). `.env.example` documents `chmod 0644 .env`.

**`.gitignore`**: `.env`, `data/inputs/*`, `data/rag_storage/*`, `data/backups/*`,
`data/prompts/*` (keep `.gitkeep` in each).

**Alternatives considered**: named Docker volumes (rejected — opaque to file tools, harder
to back up and inspect); single flat `data/` mount (rejected — loses the category
boundaries the constitution and spec require).

---

## Decision 8 — Backup & restore strategy

**Decision**:
- **Backup** (`scripts/backup.sh`): quiesce the service (`docker compose stop`), create
  `data/backups/kb-<UTC-timestamp>.tar.gz` containing `data/rag_storage/` in full
  (optionally `data/inputs/` with `--with-inputs`), write a sidecar
  `kb-<timestamp>.manifest` recording image tag, `EMBEDDING_MODEL`, `EMBEDDING_DIM`,
  LightRAG version from `/health`, and a checksum, then restart the service. Verify the
  archive (`tar -tzf`) before reporting success.
- **Restore** (`scripts/restore.sh <archive>`): refuse to run if `data/rag_storage/` is
  non-empty unless `--force` is passed **and** an interactive confirmation is answered
  (Principle V, FR-014, Edge Case "restore onto a live KB"). On `--force`, first move the
  existing state aside to `data/rag_storage.pre-restore-<timestamp>/` rather than deleting
  it. Stop service → extract → start → health-check. Warn if the manifest's
  `EMBEDDING_MODEL` / `EMBEDDING_DIM` differ from current `.env`.

**Rationale**:
- File-based storage means a filesystem archive of one directory is a complete, provably
  restorable capture (FR-015, FR-017, User Story 3).
- Stopping the service avoids torn JSON writes — the safest consistency guarantee without
  a database (Assumption in spec).
- Never overwriting silently, and keeping the displaced state, directly implements
  "restore should not silently overwrite" and "destructive ops require confirmation".
- The backup deliberately excludes Docker images, the Python environment, and Ollama model
  weights — all reconstructible from the repo + `ollama pull` (spec Backup constraint).

**Alternatives considered**: LightRAG application-level export (no clearly superior
built-in full-KB export exists for the file backends; filesystem archive is more complete
and simpler); live backup without stopping (rejected — risk of inconsistent JSON snapshot).

---

## Decision 9 — Operational command surface

**Decision**: One thin script per operation under `scripts/`, each a wrapper over
`docker compose` plus health/safety checks. Shared helpers in `scripts/lib/common.sh`
(loads `.env`, `set -euo pipefail`, consistent error prefix, dependency checks).

| Command | Script | Core action | Data-safety |
|---------|--------|-------------|-------------|
| start | `start.sh` | `docker compose up -d` | non-destructive |
| stop | `stop.sh` | `docker compose down` (keeps bind mounts) | non-destructive |
| restart | `restart.sh` | `docker compose restart` | non-destructive |
| health | `health.sh` | `/health` + Ollama reachability + model presence + `rag_storage` write test | read-only |
| logs | `logs.sh` | `docker compose logs -f --tail=200` | read-only |
| ingest | `ingest.sh` | `POST /documents/scan`, poll `/documents/track_status` / pipeline status | additive only |
| backup | `backup.sh` | Decision 8 | read-only wrt KB |
| restore | `restore.sh` | Decision 8 | guarded + confirmation |
| update | `update.sh` | backup → `docker compose pull` → `up -d` → health | backup first |

**Rationale**: matches the spec's required command list and the user's proposed structure;
"use Docker Compose rather than duplicating container management"; "fail clearly, never
destroy data" (Principle VII, FR-013, FR-019). No machine-specific paths, ports, or model
names in any script — all sourced from `.env`.

**Alternatives considered**: a single `kb` dispatcher subcommand script — viable but the
separate-scripts layout the user proposed is equally clear and easier to read one at a
time; kept as-is.

---

## Decision 10 — Authentication posture

**Decision**: No authentication. Leave `LIGHTRAG_API_KEY`, `AUTH_ACCOUNTS`, and
`TOKEN_SECRET` unset. Access control = loopback binding only (clarification Q4, FR-028a).

**Rationale**: single user, single machine, service bound to `127.0.0.1`. Upstream's
security warning about a public `0.0.0.0` bind does not apply because we never bind
`0.0.0.0`. `.env.example` carries a prominent comment: *adding network exposure requires
setting `LIGHTRAG_API_KEY` first.*

**Consequence**: `/health` returns only its basic (unauthenticated) payload. `health.sh`
must not depend on the authenticated-only diagnostic fields.

**Alternatives considered**: set a disabled-by-default API key anyway (rejected per the
clarification answer — keep the initial system minimal).

---

## Decision 11 — End-to-end validation

**Decision**: `scripts/smoke-test.sh` performs, in order, failing loudly at the first
broken step (FR-031, SC-011):

1. `start.sh`; wait for `/health` 200.
2. Assert Web UI reachable — `GET http://localhost:9621/` returns 200 and HTML.
3. Assert API reachable — `GET http://localhost:9621/docs` returns 200.
4. Place a known `.md` and a known `.pdf` fixture into `data/inputs/`; `ingest.sh`;
   wait for both to reach `processed`.
5. `POST /query` with a question answerable only from a fixture; assert the answer
   contains the expected fact and a source reference.
6. `restart.sh` (full `down` + `up`, i.e. container recreation); re-run the query;
   assert the same fact returns with **no** re-ingestion (doc_status still `processed`).
7. `backup.sh`; assert archive exists and `tar -tzf` lists `rag_storage`.
8. Move `data/rag_storage` aside; `restore.sh <archive>`; re-run the query; assert the
   fact returns.
9. Restore the pre-test state; report PASS/FAIL summary per check.

Fixtures live in `tests/fixtures/` and are committed (they contain no secrets).

**Rationale**: exercises every one of the eight capabilities the spec enumerates, on the
real target machine, with the real CPU model — turning SC-011 and SC-012 into an
executable check rather than a claim (Principle VIII).

---

## Implementation-time findings (T034, on the target machine, LightRAG v1.5.6)

- **Image**: pinned `ghcr.io/hkuds/lightrag:v1.5.6`, digest
  `sha256:ab23a9c83a735901b18c8960b6b482b602d5b6291abb7e07c5776f7bb2da504e`.
- **No startup auto-scan** exists in v1.5.6 and there is no `AUTO_SCAN_AT_STARTUP`
  key — FR-002a is met by upstream default behaviour.
- **`/health` config echo is authenticated-only.** Unauthenticated `/health`
  returns `status`, `core_version`, `api_version`, `auth_mode`, `webui_*`,
  `pipeline_busy/active`. `health.sh` therefore probes Ollama + storage directly.
- **`GET /` → 307 → `/webui/`.** The smoke test follows the redirect for C2.
- **`OLLAMA_LLM_NUM_PREDICT` must be set (Decision 4 amendment).** With it unset,
  `qwen2.5:3b-instruct` entered a repetition loop on LightRAG's extraction prompt
  and generated until the 600s timeout, failing the document. Setting
  `OLLAMA_LLM_NUM_PREDICT=8192` fixed it — the fixtures then ingested in ~30–60s
  each. `.env.example` ships this plus `LLM_TIMEOUT=1200`. For dense real
  documents, a 7B–8B model or a lower cap may be needed (documented in
  `docs/MODEL_SELECTION.md`).
- **LightRAG archives processed source files** into `data/inputs/__parsed__/`;
  `ingest.sh` excludes that directory from its file classification.
- **Ingestion API shapes**: `POST /documents/scan` →
  `{status:"scanning_started"|"scanning_skipped_pipeline_busy", track_id}`;
  wait on `GET /documents/scan/status/{id}` (`running`→`completed`), then
  `GET /documents/track_status/{id}` reading `.documents[].status` (lowercase
  `DocStatus` values; `status_summary` keys are `"DocStatus.X"` and unused).

## Post-implementation revision — GPU performance profile (2026-08-30)

A later performance pass found the target box has an **NVIDIA GTX 1660 Ti (6 GB)**
that Decisions 2–4 had ignored ("CPU only"). Measured findings on that box:

- At `OLLAMA_LLM_NUM_CTX=32768` Ollama's scheduler split `qwen2.5:3b` ~9 % CPU /
  91 % GPU. The split **stalls the pipeline** — CPU and GPU each idle waiting on
  the other; utilisation sat at 2–19 %. This is the reported slowness.
- Forcing full GPU offload (`OLLAMA_LLM_NUM_GPU=99`) at `NUM_CTX=16384`: both
  `qwen2.5:3b` and `bge-m3` fit together in VRAM (5.0 / 6.1 GB); generation
  60 → 79 tok/s. Prompt-processing ceiling on this card is ~300 tok/s regardless
  of `num_batch`, so the **query context budget** (`MAX_TOTAL_TOKENS`, default
  30000 ≈ 90–110 s of prompt processing) is the dominant query-latency term.

Revisions (all in `.env` / new `ollama/ollama.service`; no re-index, no topology
change — Principle IX still holds, this is configuration):

- **Decision 2 amendment**: Ollama is now a tuned **systemd user service**
  (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_KEEP_ALIVE=30m`,
  `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_NUM_PARALLEL=1`). Still `OLLAMA_HOST=
  127.0.0.1:11434`, still loopback, still unexposed — the exposure model is
  unchanged.
- **Decision 3/4 amendment**: `OLLAMA_LLM_NUM_GPU`/`OLLAMA_EMBEDDING_NUM_GPU=99`;
  `OLLAMA_LLM_NUM_CTX` 32768→12288; `OLLAMA_EMBEDDING_NUM_CTX` 8192→2048;
  `OLLAMA_LLM_NUM_PREDICT` 8192→3072 plus `OLLAMA_LLM_REPEAT_PENALTY=1.15` (loop
  guard at source); `MAX_ASYNC_LLM`/`MAX_PARALLEL_INSERT` 2/1→2/1 (unchanged);
  `EMBEDDING_BATCH_NUM=10`, `EMBEDDING_TIMEOUT=120`; `ENABLE_LLM_CACHE` false→true.
  Query context trimmed (`MAX_TOTAL_TOKENS=10000`, `TOP_K=20`, `CHUNK_TOP_K=8`, …).
  Ingestion set to an aggressive-speed profile (`MAX_GLEANING=0`, `CHUNK_SIZE=2000`).
- **Follow-up correction (2026-08-31)**: the first cut used `OLLAMA_NUM_PARALLEL=2`
  and larger contexts. On this 6 GB card that made the LLM and `bge-m3` unable to
  co-reside: Ollama evicted and reloaded them against each other on every ingest
  phase switch, and a merge-phase embedding batch queued behind a reload tripped
  LightRAG's embedding-worker timeout (2× `EMBEDDING_TIMEOUT`), halting the
  pipeline with *"Embedding func: Worker execution timeout after 60s"* (the earlier
  default `EMBEDDING_TIMEOUT=30`, so 2× = 60 s). Fixed by
  `OLLAMA_NUM_PARALLEL=1` + smaller contexts + `EMBEDDING_BATCH_NUM=10` +
  `EMBEDDING_TIMEOUT=120`. Verified: both models sit at `100% GPU` together
  (~4.8 / 6.1 GB), no eviction, ingestion completes.
- SC-012 ("no GPU required") is still satisfiable — restoring the CPU-only `.env`
  from git history reverts the profile. The GPU is used because it is present and
  helps, not because the design requires it.
- Full rationale and the fallback ladder: `docs/MODEL_SELECTION.md` →
  "Performance tuning". Applied by `scripts/apply-perf-tuning.sh`.

## Open items to confirm during implementation (not blocking)

- Exact current default value of `LIGHTRAG_PARSER` routing and whether `.txt` needs it set
  explicitly in the pinned image version — verify against that version's `env.example`.
- Whether the pinned image version's `/health` payload exposes enough unauthenticated
  fields for `health.sh`, or whether the script should probe bindings indirectly.
- Confirm `bge-m3` native dimension (1024) for the Ollama tag actually pulled, and adjust
  `EMBEDDING_DIM` if a quantised tag differs.
- Confirm the pinned image tag digest and record it in `docs/ARCHITECTURE.md`.

---

## Sources

- LightRAG repository — https://github.com/HKUDS/LightRAG
- Docker deployment guide — https://github.com/HKUDS/LightRAG/blob/main/docs/DockerDeployment.md
- Reference compose file — https://raw.githubusercontent.com/HKUDS/LightRAG/main/docker-compose.yml
- Environment reference — https://github.com/HKUDS/LightRAG/blob/main/env.example
- Full compose env reference — https://github.com/HKUDS/LightRAG/blob/main/env.docker-compose-full
- API server guide — https://github.com/HKUDS/LightRAG/blob/main/docs/LightRAG-API-Server.md
- File processing pipeline — https://github.com/HKUDS/LightRAG/blob/main/docs/FileProcessingPipeline.md
- Configuration & binding options (DeepWiki mirror) — https://deepwiki.com/HKUDS/LightRAG
- Ollama context length guidance — https://localllm.in/blog/local-llm-increase-context-length-ollama
