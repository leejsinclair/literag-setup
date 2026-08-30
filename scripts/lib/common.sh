#!/usr/bin/env bash
# scripts/lib/common.sh — shared library for every LiteRAG operational script.
#
# Source it, do not execute it:   source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
#
# Provides: strict mode, repo-root resolution, `.env` loading, dependency
# preflight, uniform exit codes, error/log helpers, a `compose` wrapper, and
# `wait_for_health`. See contracts/operational-cli.md → "Shared contract".

# Idempotent: a script that sources this twice is a no-op the second time.
if [[ -n "${_LITERAG_COMMON_SH:-}" ]]; then
  return 0
fi
_LITERAG_COMMON_SH=1

set -euo pipefail
IFS=$'\n\t'

# --- Uniform exit codes (contracts/operational-cli.md) ----------------------
readonly EXIT_OK=0          # success
readonly EXIT_FAIL=1        # operation attempted, did not succeed
readonly EXIT_USAGE=2       # bad / missing arguments
readonly EXIT_PREREQ=3      # unmet prerequisite (missing tool, missing .env, daemon down)
readonly EXIT_REFUSED=4     # refused for safety (would overwrite/destroy data)

# --- Output discipline: progress + errors to stderr, results to stdout ------
log()  { printf '%s\n' "$*" >&2; }
err()  { printf 'ERROR: %s\n' "$*" >&2; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

# die <exit-code> <message...>
die() {
  local code="${1:-$EXIT_FAIL}"; shift || true
  err "$*"
  exit "$code"
}

# --- Repo root (resolved from this file's location, CWD-independent) --------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly REPO_ROOT
readonly COMPOSE_FILE="$REPO_ROOT/compose.yaml"
readonly ENV_FILE="$REPO_ROOT/.env"
readonly ENV_EXAMPLE="$REPO_ROOT/.env.example"

# --- .env loader ----------------------------------------------------------
# Parses KEY=VALUE lines the way Docker Compose / python-dotenv do: no shell
# expansion, comments and blank lines ignored, one layer of matching surrounding
# quotes stripped. Values are exported so `docker compose` and curl helpers see
# them. (LightRAG itself reads the mounted /app/.env directly.)
load_env() {
  [[ -f "$ENV_FILE" ]] || die "$EXIT_PREREQ" \
    "no .env file at $ENV_FILE — create it from the template: cp .env.example .env"

  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"                 # left-trim
    [[ -z "$line" || "$line" == '#'* ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key#export }"
    key="${key%"${key##*[![:space:]]}"}"                    # right-trim key
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    val="${val#"${val%%[![:space:]]*}"}"                    # left-trim value
    val="${val%"${val##*[![:space:]]}"}"                    # right-trim value
    if [[ ${#val} -ge 2 && ( ${val:0:1} == '"' && ${val: -1} == '"' \
        || ${val:0:1} == "'" && ${val: -1} == "'" ) ]]; then
      val="${val:1:${#val}-2}"
    fi
    export "$key=$val"
  done < "$ENV_FILE"

  # Defaults for anything the scripts rely on that the user may have removed.
  export PORT="${PORT:-9621}"
  export HOST="${HOST:-127.0.0.1}"
  export LLM_BINDING_HOST="${LLM_BINDING_HOST:-http://localhost:11434}"
  export EMBEDDING_BINDING_HOST="${EMBEDDING_BINDING_HOST:-$LLM_BINDING_HOST}"
  export HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"
}

# --- Dependency preflight -------------------------------------------------
# All scripts need these five tools; container-lifecycle scripts additionally
# call require_docker_daemon. Missing prerequisite ⇒ exit 3 naming what's missing.
require_tools() {
  local missing=()
  local t
  for t in docker curl jq tar; do
    command -v "$t" >/dev/null 2>&1 || missing+=("$t")
  done
  if command -v docker >/dev/null 2>&1; then
    docker compose version >/dev/null 2>&1 || missing+=("docker-compose-v2-plugin")
  fi
  if (( ${#missing[@]} > 0 )); then
    die "$EXIT_PREREQ" "missing required tool(s): ${missing[*]} — install them (pacman -S ${missing[*]}) and retry"
  fi
}

require_docker_daemon() {
  docker info >/dev/null 2>&1 || die "$EXIT_PREREQ" \
    "the Docker daemon is not reachable — start it (systemctl start docker) and ensure your user is in the 'docker' group"
}

# --- docker compose wrapper (compose.yaml is the only entry point) ---------
# No direct docker run/rm/stop <id> anywhere (operational model constraint).
compose() {
  docker compose --project-directory "$REPO_ROOT" -f "$COMPOSE_FILE" "$@"
}

# True if the lightrag service has a running container.
service_running() {
  local id
  id="$(compose ps -q lightrag 2>/dev/null || true)"
  [[ -n "$id" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null || echo false)" == "true" ]]
}

# --- Health readiness poll ------------------------------------------------
# wait_for_health [timeout_seconds]
# Polls GET http://127.0.0.1:${PORT}/health until HTTP 200 or the timeout.
# Returns 0 on 200, 1 on timeout. Progress goes to stderr.
wait_for_health() {
  local timeout="${1:-${HEALTH_TIMEOUT_SECONDS:-120}}"
  local url="http://127.0.0.1:${PORT:-9621}/health"
  local start now
  start="$(date +%s)"
  log "waiting for ${url} (up to ${timeout}s) ..."
  while :; do
    if curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null; then
      log "health endpoint is responding"
      return 0
    fi
    now="$(date +%s)"
    if (( now - start >= timeout )); then
      return 1
    fi
    sleep 3
  done
}

# curl helper: GET a URL, print body to stdout, fail (non-zero) on HTTP >= 400.
http_get() { curl -fsS --max-time "${HTTP_MAX_TIME:-30}" "$@"; }

# --- Auto-run on source --------------------------------------------------
# Every script wants repo root + .env + the five tools. The Docker daemon check
# is opt-in (require_docker_daemon) so read-only helpers that don't touch Docker
# still work if it's down.
require_tools
load_env
