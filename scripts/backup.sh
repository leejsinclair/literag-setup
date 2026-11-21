#!/usr/bin/env bash
# scripts/backup.sh — create a complete, verified backup of the knowledge base.
#
# Contract: contracts/operational-cli.md → backup.sh ; research.md Decision 8
#   Args:   [--with-inputs] [--output DIR]   (default DIR = data/backups/)
#   0  archive written and verified; absolute path printed to stdout
#   1  a step failed — partial archive removed, service returned to prior state
#   2  bad arguments, or BACKUP_RETENTION is not an integer >= 1
#   3  unmet prerequisite
#
# Captures all of data/rag_storage/ (graph, vectors, KV, doc-status ledger) —
# everything needed for a full restore (FR-017). Excludes Docker images, the
# Python environment and Ollama model weights by design (all reconstructible).
# The service is stopped for the snapshot to avoid torn JSON writes, then
# returned to whatever state it was in.
#
# Spec 002 (contracts/kb-cli.md → "Change to scripts/backup.sh"; FR-005, FR-033):
#   - data/kb-sync/ (kb-sync records, run logs, plan) is always archived next to
#     data/rag_storage/, recorded in the manifest, and checked by verification.
#   - After a verified backup to the default data/backups/ (never with --output),
#     prune to BACKUP_RETENTION: the newest N kb-<timestamp>.tar.gz (+ .manifest)
#     by name timestamp, and the newest N files by mtime in each
#     data/inputs/<source>/.removed/. The archive just written is never a
#     candidate; no other file is touched. Each deletion is logged to stderr as
#     `pruned <path>`. A failed or unverified backup prunes nothing.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

with_inputs=0
default_output_dir="$REPO_ROOT/data/backups"
output_dir="$default_output_dir"
output_given=0

usage() { printf 'Usage: backup.sh [--with-inputs] [--output DIR]\n' >&2; }
while (( $# )); do
  case "$1" in
    --with-inputs) with_inputs=1; shift ;;
    --output) output_dir="${2:-}"; [[ -n "$output_dir" ]] || { usage; die "$EXIT_USAGE" "--output needs a directory"; }; output_given=1; shift 2 ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    *) usage; die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done

# FR-033: an integer of at least 1 (load_env defaults it to 14).
[[ "$BACKUP_RETENTION" =~ ^[0-9]+$ ]] && (( 10#$BACKUP_RETENTION >= 1 )) \
  || die "$EXIT_USAGE" "invalid .env key BACKUP_RETENTION: must be an integer >= 1 (got '${BACKUP_RETENTION}')"
retention=$(( 10#$BACKUP_RETENTION ))

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

# sqlite_user_version <db>: the schema version kb-sync stores in SQLite's
# user_version header field (big-endian uint32 at byte 60). Read from the header
# so no sqlite3 binary is needed; prints "unknown" for a missing or non-SQLite file.
sqlite_user_version() {
  local db="$1" b
  if [[ -f "$db" ]] && [[ "$(head -c 15 "$db" 2>/dev/null)" == "SQLite format 3" ]]; then
    read -r -a b < <(od -An -tu1 -j60 -N4 "$db")
    if (( ${#b[@]} == 4 )); then
      printf '%s\n' $(( (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3] ))
      return
    fi
  fi
  printf 'unknown\n'
}

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
# FR-005: kb-sync records travel with the knowledge base they describe.
kb_sync_records=false
kb_sync_schema_version=none
if [[ -d "$REPO_ROOT/data/kb-sync" ]]; then
  tar_paths+=( "data/kb-sync" )
  kb_sync_records=true
  kb_sync_schema_version="$(sqlite_user_version "$REPO_ROOT/data/kb-sync/kb-sync.db")"
else
  warn "data/kb-sync/ does not exist — archiving the knowledge base without kb-sync records"
fi
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
kb_sync_records=${kb_sync_records}
kb_sync_schema_version=${kb_sync_schema_version}
EOF

log "verifying archive ..."
# Capture the listing first — piping `tar | grep -q` under `set -o pipefail`
# makes grep close the pipe early, SIGPIPE tar, and fail the pipeline spuriously.
archive_listing="$(tar -tzf "$archive")"
grep -q '^\(\./\)\?data/rag_storage/' <<<"$archive_listing" \
  || die "$EXIT_FAIL" "verification failed: data/rag_storage/ missing from $archive"
if [[ "$kb_sync_records" == true ]]; then
  grep -q '^\(\./\)\?data/kb-sync/' <<<"$archive_listing" \
    || die "$EXIT_FAIL" "verification failed: data/kb-sync/ missing from $archive"
fi
[[ "$(sha256sum "$archive" | awk '{print $1}')" == "$sha" ]] \
  || die "$EXIT_FAIL" "verification failed: archive checksum is unstable"

restore_running_state
trap - EXIT

# --- FR-033 retention -------------------------------------------------------
# Runs only here: after verification passed and the EXIT trap is cleared, so a
# pruning hiccup can never remove the archive just written. Failures warn only.
prune_file() {
  if rm -f -- "$1"; then
    printf 'pruned %s\n' "$1" >&2
  else
    warn "could not prune $1"
  fi
}

prune_backups() {
  local dir="$1" keep=$(( retention - 1 )) name kept=0
  # Newest first by the timestamp in the name; the new archive is excluded and
  # counts as one of the N kept.
  while IFS= read -r -d '' name; do
    [[ "$dir/$name" == "$archive" ]] && continue
    if (( kept < keep )); then
      kept=$(( kept + 1 ))
      continue
    fi
    prune_file "$dir/$name"
    [[ -f "$dir/${name%.tar.gz}.manifest" ]] && prune_file "$dir/${name%.tar.gz}.manifest"
  done < <(find "$dir" -maxdepth 1 -type f -regextype posix-extended \
             -regex '.*/kb-[0-9]{8}T[0-9]{6}Z\.tar\.gz' -printf '%f\0' | sort -z -r)
}

prune_removed() {
  local dir="$1" entry kept=0
  # Newest first by mtime.
  while IFS= read -r -d '' entry; do
    if (( kept < retention )); then
      kept=$(( kept + 1 ))
      continue
    fi
    prune_file "${entry#*$'\t'}"
  done < <(find "$dir" -maxdepth 1 -type f -printf '%T@\t%p\0' | sort -z -t $'\t' -k1,1 -r -n)
}

if (( output_given )); then
  log "--output given: retention pruning skipped"
else
  prune_backups "$(cd "$default_output_dir" && pwd)"
  for removed_dir in "$REPO_ROOT"/data/inputs/*/.removed; do
    [[ -d "$removed_dir" && ! -L "$removed_dir" ]] || continue
    prune_removed "$removed_dir"
  done
fi

log "-------------------------------------------"
log "backup complete and verified"
log "manifest: $manifest"
printf '%s\n' "$archive"
exit "$EXIT_OK"
