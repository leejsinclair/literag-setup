#!/usr/bin/env bash
# scripts/restore.sh — restore the knowledge base from a backup archive.
#
# Contract: contracts/operational-cli.md → restore.sh ; FR-014, Principle V
#   Args:   <archive-path> [--force] [--assume-yes] [--with-inputs]
#   0  service healthy on the restored KB; prints displaced-dir path (if any)
#   1  extract/health failed (rollback offered)
#   2  bad arguments / unreadable archive
#   3  unmet prerequisite
#   4  refused: data/rag_storage/ is non-empty and --force was not given
#
# Never deletes prior KB state — it is MOVED to
# data/rag_storage.pre-restore-<timestamp>/ . Any overwrite needs --force AND a
# confirmation (interactive, or --assume-yes on non-interactive stdin).

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

usage() { printf 'Usage: restore.sh <archive-path> [--force] [--assume-yes] [--with-inputs]\n' >&2; }

archive=""
force=0
assume_yes=0
with_inputs=0
while (( $# )); do
  case "$1" in
    --force) force=1; shift ;;
    --assume-yes) assume_yes=1; shift ;;
    --with-inputs) with_inputs=1; shift ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    -*) usage; die "$EXIT_USAGE" "unknown option: $1" ;;
    *) [[ -z "$archive" ]] || { usage; die "$EXIT_USAGE" "unexpected extra argument: $1"; }; archive="$1"; shift ;;
  esac
done
[[ -n "$archive" ]] || { usage; die "$EXIT_USAGE" "an archive path is required"; }
[[ -f "$archive" ]] || die "$EXIT_USAGE" "archive not found: $archive"
archive="$(cd "$(dirname "$archive")" && pwd)/$(basename "$archive")"

require_docker_daemon

# --- 1. Validate the archive ---------------------------------------------
log "validating archive ..."
archive_listing="$(tar -tzf "$archive" 2>/dev/null)" \
  || die "$EXIT_FAIL" "not a readable gzip tar archive: $archive"
grep -q '^\(\./\)\?data/rag_storage/' <<<"$archive_listing" \
  || die "$EXIT_FAIL" "archive does not contain data/rag_storage/ — wrong file?"
archive_has_inputs=0
grep -q '^\(\./\)\?data/inputs/' <<<"$archive_listing" && archive_has_inputs=1 || true
if (( with_inputs && ! archive_has_inputs )); then
  die "$EXIT_USAGE" "--with-inputs given but the archive has no data/inputs/ (was it made without --with-inputs?)"
fi

# --- 2. Manifest comparison -------------------------------------------
manifest="${archive%.tar.gz}.manifest"
mismatch_confirm_needed=0
if [[ -f "$manifest" ]]; then
  m_emb_model="$(sed -n 's/^embedding_model=//p' "$manifest")"
  m_emb_dim="$(sed -n 's/^embedding_dim=//p' "$manifest")"
  m_img_tag="$(sed -n 's/^image_tag=//p' "$manifest")"
  m_core_ver="$(sed -n 's/^lightrag_core_version=//p' "$manifest")"

  if [[ -n "$m_emb_model" && "$m_emb_model" != "${EMBEDDING_MODEL:-}" ]] \
     || [[ -n "$m_emb_dim" && "$m_emb_dim" != "${EMBEDDING_DIM:-}" ]]; then
    warn "EMBEDDING MISMATCH:"
    warn "  backup:  model=${m_emb_model:-?}  dim=${m_emb_dim:-?}"
    warn "  .env:    model=${EMBEDDING_MODEL:-?}  dim=${EMBEDDING_DIM:-?}"
    warn "  The restored vector index was built for the backup's embedding model."
    warn "  After restoring you must either set .env back to match, or re-index"
    warn "  (docs/MODEL_SELECTION.md). Stale results must not be treated as current."
    mismatch_confirm_needed=1
  fi
  if [[ -n "$m_img_tag" && "$m_img_tag" != "${LIGHTRAG_IMAGE_TAG:-}" ]]; then
    warn "image tag skew: backup made on '${m_img_tag}', .env now pins '${LIGHTRAG_IMAGE_TAG}' (core_version ${m_core_ver:-?}). Restore continues; watch the logs on first start."
  fi
else
  warn "no sidecar manifest ($manifest) — cannot check embedding/version compatibility"
fi

# --- confirmation helper -------------------------------------------------
confirm() {
  local prompt="$1"
  if [[ -t 0 ]]; then
    local ans=""
    read -r -p "$prompt [yes/no] " ans
    [[ "$ans" == "yes" || "$ans" == "y" ]]
  else
    (( assume_yes )) || die "$EXIT_REFUSED" "non-interactive stdin and no --assume-yes — refusing without confirmation"
    log "$prompt -> assuming yes (--assume-yes)"
    return 0
  fi
}

# --- 3. Guard: non-empty live knowledge base ----------------------------
kb="$REPO_ROOT/data/rag_storage"
kb_nonempty=0
if [[ -d "$kb" ]] && [[ -n "$(find "$kb" -mindepth 1 ! -name .gitkeep -print -quit 2>/dev/null)" ]]; then
  kb_nonempty=1
fi

if (( kb_nonempty )); then
  if (( ! force )); then
    die "$EXIT_REFUSED" "refusing to overwrite an existing knowledge base at data/rag_storage/ — re-run with --force to move it aside and restore"
  fi
  confirm "This will move the current data/rag_storage/ aside and restore from ${archive##*/}. Continue?" \
    || die "$EXIT_REFUSED" "restore cancelled"
fi
if (( mismatch_confirm_needed )); then
  confirm "Proceed despite the embedding mismatch above?" || die "$EXIT_REFUSED" "restore cancelled"
fi

# --- 4. Stop the service ---------------------------------------------
was_running=0
service_running && was_running=1
if (( was_running )); then
  log "stopping lightrag ..."
  compose stop
fi

# --- 5. Displace existing state (never delete) -------------------------
displaced=""
if (( kb_nonempty )); then
  displaced="$REPO_ROOT/data/rag_storage.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
  log "moving current knowledge base aside -> $displaced"
  mv "$kb" "$displaced"
  mkdir -p "$kb"
fi

rollback() {
  err "restore failed — rolling back"
  rm -rf "$kb"
  if [[ -n "$displaced" && -d "$displaced" ]]; then
    mv "$displaced" "$kb"
    log "previous knowledge base restored from $displaced"
  fi
  if (( was_running )); then compose up -d >/dev/null 2>&1 || true; fi
}

# --- 6. Extract -----------------------------------------------------
log "extracting archive ..."
extract_paths=( "data/rag_storage" )
(( with_inputs )) && extract_paths+=( "data/inputs" )
if ! tar -xzf "$archive" -C "$REPO_ROOT" "${extract_paths[@]}"; then
  rollback
  die "$EXIT_FAIL" "extraction failed"
fi

# --- 7. Start + health-check ------------------------------------------
log "starting lightrag on the restored knowledge base ..."
if ! compose up -d; then
  rollback
  die "$EXIT_FAIL" "service failed to start after restore"
fi
if ! wait_for_health "${HEALTH_TIMEOUT_SECONDS}"; then
  err "lightrag did not become healthy on the restored knowledge base"
  if [[ -n "$displaced" ]]; then
    log "to roll back manually: ./scripts/stop.sh && rm -rf data/rag_storage && mv '$displaced' data/rag_storage && ./scripts/start.sh"
  fi
  exit "$EXIT_FAIL"
fi

log "-------------------------------------------"
log "restore complete; lightrag is healthy"
if [[ -n "$displaced" ]]; then
  log "previous knowledge base preserved at: $displaced"
  log "delete it once you have confirmed the restore is good."
  printf '%s\n' "$displaced"
fi
exit "$EXIT_OK"
