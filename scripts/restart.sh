#!/usr/bin/env bash
# scripts/restart.sh — restart the LightRAG service, then wait for health.
#
# Contract: contracts/operational-cli.md → restart.sh
#   Args:   [--recreate] [--timeout SECONDS]
#           default      → docker compose restart (same container)
#           --recreate   → docker compose down then up -d (full recreation)
#   0  /health returned 200 after the restart
#   1  timed out waiting for health
#   3  unmet prerequisite
#
# Non-destructive. Never passes -v/--volumes. `smoke-test.sh` uses --recreate to
# prove the knowledge base survives container recreation (FR-012, SC-005).
#
# The image-replacement case (FR-012, SC-005) can be verified manually with:
#     docker compose pull && ./scripts/restart.sh --recreate
#     ./scripts/ingest.sh        # expect: 0 processed / N skipped / 0 failed
# scripts/update.sh automates exactly this path with a backup taken first.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

usage() { printf 'Usage: restart.sh [--recreate] [--timeout SECONDS]\n' >&2; }

recreate=0
timeout="${HEALTH_TIMEOUT_SECONDS}"
while (( $# )); do
  case "$1" in
    --recreate) recreate=1; shift ;;
    --timeout)  timeout="${2:-}"; [[ -n "$timeout" ]] || { usage; die "$EXIT_USAGE" "--timeout needs a value"; }; shift 2 ;;
    -h|--help)  usage; exit "$EXIT_OK" ;;
    *) usage; die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done
[[ "$timeout" =~ ^[0-9]+$ ]] || die "$EXIT_USAGE" "--timeout must be an integer number of seconds"

require_docker_daemon

if (( recreate )); then
  log "recreating the lightrag container (down + up -d) ..."
  compose down
  compose up -d
else
  log "restarting the lightrag container ..."
  compose restart lightrag
fi

if wait_for_health "$timeout"; then
  log "lightrag is healthy after restart"
  printf 'http://127.0.0.1:%s\n' "${PORT}"
  exit "$EXIT_OK"
fi

err "lightrag did not become healthy within ${timeout}s after restart"
log "---------------- last 40 log lines ----------------"
compose logs --tail 40 lightrag >&2 || true
log "--------------------------------------------------"
log "diagnose with: ./scripts/health.sh"
exit "$EXIT_FAIL"
