#!/usr/bin/env bash
# scripts/stop.sh — stop and remove the LightRAG container (keep all data).
#
# Contract: contracts/operational-cli.md → stop.sh
#   Args:   none
#   0  container stopped and removed; data/ untouched
#   3  unmet prerequisite
#
# Runs `docker compose down` (containers + network only). It MUST NOT accept or
# pass -v/--volumes — the knowledge base lives in host bind mounts under data/
# and is never touched by this command (Invariant 1, FR-013).

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

if (( $# )); then
  case "$1" in
    -h|--help) printf 'Usage: stop.sh   (no arguments)\n' >&2; exit "$EXIT_OK" ;;
    *) die "$EXIT_USAGE" "stop.sh takes no arguments (got: $1). It never removes volumes or data." ;;
  esac
fi

require_docker_daemon

if ! service_running && [[ -z "$(compose ps -aq lightrag 2>/dev/null || true)" ]]; then
  log "lightrag is not running — nothing to stop"
  exit "$EXIT_OK"
fi

log "stopping lightrag (docker compose down — bind mounts under data/ are retained) ..."
compose down

# Reassure the operator that the knowledge base is still on disk.
kb="$REPO_ROOT/data/rag_storage"
if [[ -d "$kb" ]]; then
  log "knowledge base retained at data/rag_storage/ ($(du -sh "$kb" 2>/dev/null | cut -f1 || echo '?') on disk)"
fi
log "stopped."
exit "$EXIT_OK"
