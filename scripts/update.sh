#!/usr/bin/env bash
# scripts/update.sh — back up, then pull and recreate on a new image.
#
# Contract: contracts/operational-cli.md → update.sh ; FR-018
#   Args:   [--tag IMAGE_TAG]
#           no --tag  → pull the tag currently in .env (refresh to its digest)
#           --tag X   → update to X; .env's LIGHTRAG_IMAGE_TAG is rewritten
#                       ONLY after the new image is confirmed healthy
#   0  new image running and healthy; prints the backup archive path used
#   1  backup failed (update aborted), or the new image was unhealthy
#   3  unmet prerequisite
#
# Always backs up first (Principle V). Never removes the previous image.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

usage() { printf 'Usage: update.sh [--tag IMAGE_TAG]\n' >&2; }

new_tag=""
while (( $# )); do
  case "$1" in
    --tag) new_tag="${2:-}"; [[ -n "$new_tag" ]] || { usage; die "$EXIT_USAGE" "--tag needs a value"; }; shift 2 ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    *) usage; die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done

require_docker_daemon

old_tag="${LIGHTRAG_IMAGE_TAG}"
target_tag="${new_tag:-$old_tag}"

# --- 1. Back up first; abort the update if it fails --------------------
log "step 1/4: backing up before the update ..."
if ! backup_archive="$("$REPO_ROOT/scripts/backup.sh")"; then
  die "$EXIT_FAIL" "pre-update backup failed — update aborted, nothing changed"
fi
log "backup: $backup_archive"

# --- 2. Pull the target image ----------------------------------------
# Shell env overrides .env for Compose interpolation, so we can pull a new tag
# without touching .env yet.
export LIGHTRAG_IMAGE_TAG="$target_tag"
log "step 2/4: pulling ghcr.io/hkuds/lightrag:${target_tag} ..."
if ! compose pull; then
  export LIGHTRAG_IMAGE_TAG="$old_tag"
  die "$EXIT_FAIL" "docker compose pull failed for tag '${target_tag}' — nothing changed"
fi

# --- 3. Recreate on the new image ----------------------------------
log "step 3/4: recreating the container ..."
compose up -d

# --- 4. Health check ------------------------------------------------
log "step 4/4: waiting for health ..."
if wait_for_health "${HEALTH_TIMEOUT_SECONDS}"; then
  if [[ -n "$new_tag" ]]; then
    if grep -qE '^[[:space:]]*LIGHTRAG_IMAGE_TAG=' "$ENV_FILE"; then
      sed -i -E "s|^([[:space:]]*LIGHTRAG_IMAGE_TAG=).*|\1${new_tag}|" "$ENV_FILE"
    else
      printf '\nLIGHTRAG_IMAGE_TAG=%s\n' "$new_tag" >> "$ENV_FILE"
    fi
    log "updated .env: LIGHTRAG_IMAGE_TAG=${new_tag}"
  fi
  log "-------------------------------------------"
  log "update complete; lightrag is healthy on ghcr.io/hkuds/lightrag:${target_tag}"
  printf '%s\n' "$backup_archive"
  exit "$EXIT_OK"
fi

# --- Unhealthy: leave .env untouched and print rollback steps ---------
err "the new image did not become healthy — .env was NOT changed"
cat >&2 <<EOF
Roll back with:
  1. (if you passed --tag) it is already unchanged: .env still pins '${old_tag}'
  2. ./scripts/restart.sh --recreate
  3. if the knowledge base looks wrong:
       ./scripts/restore.sh '${backup_archive}' --force
Last 40 log lines:
EOF
compose logs --tail 40 lightrag >&2 || true
export LIGHTRAG_IMAGE_TAG="$old_tag"
exit "$EXIT_FAIL"
