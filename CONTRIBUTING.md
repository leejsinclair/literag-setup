# Contributing

Thanks for your interest. This repo is a **personal, single-machine deployment** of
[LightRAG](https://github.com/HKUDS/LightRAG), not a general-purpose product. You are
welcome to **use it, fork it, and adapt it** for your own setup (see [`LICENSE.md`](LICENSE.md)).
Contributions back are welcome too, within the scope below.

## Scope

This repository *is* the deployment — there is no application source tree, only
`compose.yaml`, `.env.example`, `scripts/*.sh`, `docs/`, `tests/`, and the Spec Kit
design artifacts under `specs/`. Good contributions:

- Bug fixes in the operational scripts or `compose.yaml`
- Documentation corrections and clarifications
- Portability fixes (other distros, other GPUs, CPU-only paths)
- Additional troubleshooting entries backed by a real reproduction

Out of scope: adding services, databases, sidecars, network exposure, auth layers,
schedulers, or file-watchers. The design is deliberately minimal — see the ten
principles in `.specify/memory/constitution.md`. Any change that adds a service,
volume, dependency, or network exposure must state which principle permits it.

## Workflow

1. **Branch** off `main` — `git checkout -b <type>/<short-description>`
   (`fix/…`, `docs/…`, `feat/…`, `chore/…`). Do not commit directly to `main`.
2. Make the change. Keep commits focused; write imperative commit subjects.
3. Open a pull request against `main` describing what changed and why.
4. One approving review (or the maintainer's own merge) lands it. Squash or
   rebase-merge preferred; keep history linear.

## Ground rules

- **Spec-driven.** Design lives in `specs/001-lightrag-knowledge-base/`. Behavioural
  changes should update the relevant spec/plan/research artifact in the same PR.
- **Evidence over assumption.** Cite official LightRAG docs (pinned to the image tag)
  for any env var or endpoint — don't invent them.
- **`.env` and `.env.example` stay byte-identical.** Edit both, then verify with
  `diff <(grep -v '^#' .env) <(grep -v '^#' .env.example)`. Every `.env` comment
  must be on its own line (two parsers, one strips inline `#`, one doesn't).
- **Never re-declare an `.env` key in `compose.yaml`.** `.env` is the single
  configuration surface; `compose.yaml` has no `environment:` block.
- **Persistent data is sacred.** Nothing under `data/rag_storage/` may be deleted or
  overwritten by any command except the guarded `restore.sh`. `data/` contents,
  `.env`, and `.env.*` are gitignored — never commit knowledge-base data or secrets.

## Script idiom

Match the existing style in `scripts/`:

- `set -euo pipefail`; source `scripts/lib/common.sh` (sourced, never executed)
- Header comment citing the relevant contract + FR / Invariant
- `usage()` to stderr; uniform exit codes via `die` —
  `0` ok · `1` failed · `2` bad args · `3` unmet prerequisite · `4` refused for safety
- Progress and errors to stderr, results to stdout; `jq` for all JSON
- All Docker access through the `compose()` wrapper — no bare `docker` calls

## Testing

Run on the target machine before opening a PR that touches scripts or `compose.yaml`:

```bash
./scripts/smoke-test.sh        # end-to-end contract tests C1–C8 (uses the real Ollama model)
bats tests/unit/common.bats    # optional: unit coverage for common.sh
```

`smoke-test.sh` backs up and restores your existing knowledge base on exit.

## Reporting issues

Open a GitHub issue with your host OS, Docker/Compose versions, GPU (if any),
`./scripts/health.sh --json` output, and relevant `./scripts/logs.sh` lines.
