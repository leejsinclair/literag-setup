#!/usr/bin/env bash
# scripts/kb.sh — the owner's entry point to kb-sync (Drive, AppFlowy and research → LightRAG).
#
# Contract: specs/002-kb-sync-knowledge-pipeline/contracts/kb-cli.md (FR-031)
#   Args:   <subcommand> [args]
#     auth|pull|ask|pack|notebook|review|eval   pass through to the kb-sync container
#     ingest [--confirm-removals]               host sequence: plan → backup → health → apply
#     nightly                                   pull, then the ingest host sequence (FR-032)
#     mcp                                       exec the MCP server (stdout carries MCP)
#   0  success
#   1  failed (an upstream call, an export or an ingest failed)
#   2  bad arguments
#   3  unmet prerequisite (LightRAG stopped, or not healthy after the pre-removal backup)
#   4  refused for safety (removals held over KB_SYNC_REMOVAL_LIMIT)
#
# Removal safety (FR-010, FR-034, SC-008, SC-014): nothing is removed unless a backup was
# taken in the same run and LightRAG came back healthy after it. The container is run
# only through the `compose` wrapper, under the `kb-sync` profile, so start.sh never
# starts it (FR-003).

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIGHTRAG_URL="http://127.0.0.1:${PORT}"
READY_TIMEOUT="${HEALTH_TIMEOUT_SECONDS:-120}"

usage() {
  cat >&2 <<'EOF'
Usage: kb.sh <subcommand> [args]

  auth google|appflowy        sign in to a source (re-run to renew)
  pull [--source S]           fetch changes from the configured sources
  ingest [--confirm-removals] back up if anything is removed, then apply to LightRAG
  nightly                     pull, then ingest (the unattended job)
  ask "question" [--local] [--no-filter]
  pack <topic>                build or refresh packs/<topic>.yaml
  notebook push <topic>       push a pack to NotebookLM
  review <topic> [--web|--apply]
  eval [--filter none|reranker|local|all]
  mcp                         run the MCP server on stdio

Exit codes: 0 ok, 1 failed, 2 bad args, 3 unmet prerequisite, 4 refused for safety.
EOF
}

# kb_run <args...> — one-shot kb-sync container; its exit code is returned.
kb_run() {
  compose --profile kb-sync run --rm -T kb-sync "$@"
}

# ingest_sequence [--confirm-removals] — the FR-010/FR-034 host sequence. Returns the
# --apply exit code; dies 3 when LightRAG is stopped or does not return after the backup.
ingest_sequence() {
  local confirm=()
  while (( $# )); do
    case "$1" in
      --confirm-removals) confirm=(--confirm-removals); shift ;;
      -h|--help) usage; exit "$EXIT_OK" ;;
      *) usage; die "$EXIT_USAGE" "unknown argument to ingest: $1" ;;
    esac
  done

  require_docker_daemon
  service_running || die "$EXIT_PREREQ" \
    "LightRAG server is not running at ${LIGHTRAG_URL}. Start it with ./scripts/start.sh"

  # 1. Plan. The last stdout line is `plan_id=<id> removals=<n> held=<n>`.
  local plan_out rc=0
  plan_out="$(kb_run ingest --plan "${confirm[@]}")" || rc=$?
  (( rc == 0 )) || die "$rc" "kb-sync ingest --plan failed (exit $rc)"
  local plan_line plan_id removals
  plan_line="$(printf '%s\n' "$plan_out" | tail -n 1)"
  plan_id="$(sed -n 's/^plan_id=\([^ ]*\).*/\1/p' <<<"$plan_line")"
  removals="$(sed -n 's/.* removals=\([0-9][0-9]*\).*/\1/p' <<<"$plan_line")"
  [[ -n "$plan_id" && -n "$removals" ]] || die "$EXIT_FAIL" \
    "kb-sync ingest --plan gave no plan line (got: ${plan_line:-nothing})"
  log "plan ${plan_id}: ${removals} removal(s)"

  # 2-3. A removal needs a same-run backup, and LightRAG healthy again after it.
  local backup_args=()
  if (( removals > 0 )); then
    local archive name
    archive="$("$SCRIPT_DIR/backup.sh")" || die "$EXIT_FAIL" "backup.sh failed. Nothing was removed"
    archive="$(printf '%s\n' "$archive" | tail -n 1)"
    name="$(basename "$archive")"
    [[ -n "$name" ]] || die "$EXIT_FAIL" "backup.sh printed no archive path. Nothing was removed"
    wait_for_health "$READY_TIMEOUT" || die "$EXIT_PREREQ" \
      "LightRAG did not come back after the backup ${name}. Nothing was removed. Start it with ./scripts/start.sh and re-run scripts/kb.sh ingest"
    backup_args=(--backup "$name")
  fi

  # 4. Apply. Its exit code (0, 1, 4) passes through.
  rc=0
  kb_run ingest --apply "$plan_id" "${backup_args[@]}" "${confirm[@]}" || rc=$?

  # 5. Keep the index stamp current, shared with ingest.sh (FR-025).
  write_index_stamp "$plan_id"
  return "$rc"
}

(( $# )) || { usage; exit "$EXIT_USAGE"; }
cmd="$1"; shift

case "$cmd" in
  -h|--help|help)
    usage; exit "$EXIT_OK" ;;

  mcp)
    # Nothing may reach stdout before the server: it carries the MCP protocol.
    # `exec` cannot take the compose() function, so this spells out the same command.
    exec docker compose --project-directory "$REPO_ROOT" -f "$COMPOSE_FILE" \
      --profile kb-sync run --rm -T kb-sync mcp "$@" ;;

  ingest)
    rc=0
    ingest_sequence "$@" || rc=$?
    exit "$rc" ;;

  nightly)
    (( $# == 0 )) || { usage; die "$EXIT_USAGE" "nightly takes no arguments"; }
    require_docker_daemon
    # Pull first. A failed source does not stop the others or the ingest (FR-032).
    pull_rc=0
    kb_run pull || pull_rc=$?
    (( pull_rc == 0 )) || warn "pull finished with exit ${pull_rc}; ingesting what was pulled"
    ingest_rc=0
    ingest_sequence || ingest_rc=$?
    if (( ingest_rc == 1 || pull_rc != 0 )); then
      rc="$EXIT_FAIL"
    else
      rc="$ingest_rc"
    fi
    log "nightly: pull exit ${pull_rc}, ingest exit ${ingest_rc}, overall exit ${rc}"
    exit "$rc" ;;

  auth)
    # Interactive: Google prints a sign-in URL, AppFlowy prompts for a password. Keep the TTY.
    rc=0
    compose --profile kb-sync run --rm kb-sync auth "$@" || rc=$?
    exit "$rc" ;;

  pull|ask|pack|notebook|review|eval)
    rc=0
    kb_run "$cmd" "$@" || rc=$?
    exit "$rc" ;;

  *)
    usage; die "$EXIT_USAGE" "unknown subcommand: $cmd" ;;
esac
