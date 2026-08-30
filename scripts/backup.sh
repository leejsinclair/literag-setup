#!/usr/bin/env bash
# scripts/backup.sh — create a complete, verified backup of the knowledge base.
#
# Contract: contracts/operational-cli.md → backup.sh ; research.md Decision 8
#   Args:   [--with-inputs] [--output DIR]   (default DIR = data/backups/)
#   0  archive written and verified; absolute path printed to stdout
#   1  a step failed — partial archive removed, service returned to prior state
#   3  unmet prerequisite
#
# Captures all of data/rag_storage/ (graph, vectors, KV, doc-status ledger) —
# everything needed for a full restore (FR-017). Excludes Docker images, the
# Python environment and Ollama model weights by design (all reconstructible).
# The service is stopped for the snapshot to avoid torn JSON writes, then
# returned to whatever state it was in.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

with_inputs=0
output_dir="$REPO_ROOT/data/backups"

usage() { printf 'Usage: backup.sh [--with-inputs] [--output DIR]\n' >&2; }
while (( $# )); do
  case "$1" in
    --with-inputs) with_inputs=1; shift ;;
    --output) output_dir="${2:-}"; [[ -n "$output_dir" ]] || { usage; die "$EXIT_USAGE" "--output needs a directory"; }; shift 2 ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    *) usage; die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done

require_docker_daemon
mkdir -p "$output_dir"
[[ -w "$output_dir" ]] || die "$EXIT_FAIL" "backup output directory is not writable: $output_dir"
[[ -d "$REPO_ROOT/data/rag_storage" ]] || die "$EXIT_FAIL" "data/rag_storage/ does not exist — nothing to back up"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$(cd "$output_dir" && pwd)/kb-${ts}.tar.gz"
manifest="${archive%.tar.gz}.manifest"

was_running=0
service_running && was_running=1

# Capture the LightRAG version from /health while the service is (maybe) still up.
health_json='{}'
if (( was_running )); then
  health_json="$(http_get "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo '{}')"
fi
core_version="$(jq -r '.core_version // "unknown"' <<<"$health_json" 2>/dev/null || echo unknown)"
api_version="$(jq -r '.api_version // "unknown"' <<<"$health_json" 2>/dev/null || echo unknown)"

image_ref="ghcr.io/hkuds/lightrag:${LIGHTRAG_IMAGE_TAG}"
image_digest="$(docker inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$image_ref" 2>/dev/null || true)"
[[ -n "$image_digest" ]] || image_digest="unknown"

restore_running_state() {
  if (( was_running )); then
    log "returning lightrag to its running state ..."
    compose up -d >/dev/null 2>&1 || true
    wait_for_health 120 >/dev/null 2>&1 || warn "lightrag did not return to healthy — run ./scripts/health.sh"
  fi
}
on_exit() {
  local rc=$?
  if (( rc != 0 )); then
    err "backup failed — removing any partial artifact"
    rm -f "$archive" "$manifest"
    restore_running_state
  fi
}
trap on_exit EXIT

if (( was_running )); then
  log "stopping lightrag for a consistent snapshot ..."
  compose stop
fi

tar_paths=( "data/rag_storage" )
if (( with_inputs )) && [[ -d "$REPO_ROOT/data/inputs" ]]; then
  tar_paths+=( "data/inputs" )
fi

log "creating archive: $archive"
tar -czf "$archive" -C "$REPO_ROOT" "${tar_paths[@]}"

sha="$(sha256sum "$archive" | awk '{print $1}')"

cat > "$manifest" <<EOF
# LiteRAG knowledge-base backup manifest — created $(date -u +%Y-%m-%dT%H:%M:%SZ)
created_utc=${ts}
archive=$(basename "$archive")
archive_sha256=${sha}
with_inputs=${with_inputs}
image_tag=${LIGHTRAG_IMAGE_TAG}
image_ref=${image_ref}
image_digest=${image_digest}
lightrag_core_version=${core_version}
lightrag_api_version=${api_version}
embedding_model=${EMBEDDING_MODEL:-unknown}
embedding_dim=${EMBEDDING_DIM:-unknown}
llm_model=${LLM_MODEL:-unknown}
EOF

log "verifying archive ..."
# Capture the listing first — piping `tar | grep -q` under `set -o pipefail`
# makes grep close the pipe early, SIGPIPE tar, and fail the pipeline spuriously.
archive_listing="$(tar -tzf "$archive")"
grep -q '^\(\./\)\?data/rag_storage/' <<<"$archive_listing" \
  || die "$EXIT_FAIL" "verification failed: data/rag_storage/ missing from $archive"
[[ "$(sha256sum "$archive" | awk '{print $1}')" == "$sha" ]] \
  || die "$EXIT_FAIL" "verification failed: archive checksum is unstable"

restore_running_state
trap - EXIT

log "-------------------------------------------"
log "backup complete and verified"
log "manifest: $manifest"
printf '%s\n' "$archive"
exit "$EXIT_OK"
