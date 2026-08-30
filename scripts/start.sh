#!/usr/bin/env bash
# scripts/start.sh — bring the LightRAG service up and wait for it to be healthy.
#
# Contract: contracts/operational-cli.md → start.sh
#   Args:    [--timeout SECONDS]
#   0  /health returned 200; prints http://127.0.0.1:${PORT} to stdout
#   1  timed out waiting for health (prints log tail + hint to run health.sh)
#   3  unmet prerequisite (missing tool, no .env, Docker daemon down)
#   Idempotent: re-running while already up just re-checks health.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

usage() {
  cat >&2 <<'EOF'
Usage: start.sh [--timeout SECONDS]

Starts the lightrag service (docker compose up -d) and polls GET /health until
it returns 200. --timeout overrides HEALTH_TIMEOUT_SECONDS from .env.
EOF
}

timeout="${HEALTH_TIMEOUT_SECONDS}"
while (( $# )); do
  case "$1" in
    --timeout) timeout="${2:-}"; [[ -n "$timeout" ]] || { usage; die "$EXIT_USAGE" "--timeout needs a value"; }; shift 2 ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    *) usage; die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done
[[ "$timeout" =~ ^[0-9]+$ ]] || die "$EXIT_USAGE" "--timeout must be an integer number of seconds"

require_docker_daemon

if service_running; then
  log "lightrag is already running — re-checking health"
else
  log "starting lightrag (image: ghcr.io/hkuds/lightrag:${LIGHTRAG_IMAGE_TAG}) ..."
  compose up -d
fi

if wait_for_health "$timeout"; then
  log "lightrag is up and healthy"
  printf 'http://127.0.0.1:%s\n' "${PORT}"
  exit "$EXIT_OK"
fi

err "lightrag did not become healthy within ${timeout}s"
log "---------------- last 40 log lines ----------------"
compose logs --tail 40 lightrag >&2 || true
log "--------------------------------------------------"
log "diagnose with: ./scripts/health.sh   (is Ollama running? are the models pulled?)"
exit "$EXIT_FAIL"
