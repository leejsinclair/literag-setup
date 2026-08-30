#!/usr/bin/env bash
# scripts/ingest.sh — scan data/inputs/ and ingest new or changed documents.
#
# Contract: contracts/operational-cli.md → ingest.sh
#           contracts/lightrag-api.md §2 (POST /documents/scan), §4 (track_status), §5
#   Args:   [--wait-timeout SECONDS]   (default 1800)
#   0  every enqueued document reached `processed`
#   1  at least one document `failed`, or the pipeline was busy, or a timeout
#   3  unmet prerequisite (service not running, tool/.env missing, daemon down)
#
# Additive only — this never deletes knowledge-base content (Invariant 1).
# Safe to re-run: documents unchanged since their last successful ingestion are
# not re-enqueued by the server (FR-005, SC-006).

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

DOCS_API="http://127.0.0.1:${PORT}/documents"
POLL_INTERVAL="${INGEST_POLL_INTERVAL:-5}"
wait_timeout=1800

usage() {
  cat >&2 <<'EOF'
Usage: ingest.sh [--wait-timeout SECONDS]

Triggers POST /documents/scan on data/inputs/ and waits until every enqueued
document is `processed` or `failed`. Unchanged documents are skipped by the
server. Prints a summary and exits non-zero if any document failed.
EOF
}

while (( $# )); do
  case "$1" in
    --wait-timeout) wait_timeout="${2:-}"; [[ -n "$wait_timeout" ]] || { usage; die "$EXIT_USAGE" "--wait-timeout needs a value"; }; shift 2 ;;
    -h|--help) usage; exit "$EXIT_OK" ;;
    *) usage; die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done
[[ "$wait_timeout" =~ ^[0-9]+$ ]] || die "$EXIT_USAGE" "--wait-timeout must be an integer number of seconds"

require_docker_daemon
service_running || die "$EXIT_PREREQ" "lightrag is not running — start it first: ./scripts/start.sh"
wait_for_health 30 || die "$EXIT_FAIL" "lightrag /health is not responding — run ./scripts/health.sh"

# --- Refuse to proceed if the workspace is fenced pending recovery ----------
pipeline="$(http_get "$DOCS_API/pipeline_status" || true)"
[[ -n "$pipeline" ]] || pipeline='{}'
if [[ "$(jq -r '.recovery_required // false' <<<"$pipeline" 2>/dev/null || echo false)" == "true" ]]; then
  err "the workspace is fenced pending recovery: $(jq -r '.recovery_message // "see docs/TROUBLESHOOTING.md"' <<<"$pipeline")"
  die "$EXIT_FAIL" "resolve the recovery fence before ingesting (docs/TROUBLESHOOTING.md → interrupted ingestion)"
fi

# --- Classify source files (host side) for the summary --------------------
# LightRAG archives each processed source file into data/inputs/__parsed__/ (with
# per-doc *.parsed/ sidecar dirs). Those are not user inputs — exclude them.
input_dir="$REPO_ROOT/data/inputs"
total_files=0 supported_count=0 unsupported_count=0
if [[ -d "$input_dir" ]]; then
  mapfile -t _files < <(find "$input_dir" -type f ! -name '.gitkeep' \
    -not -path '*/__parsed__/*' -not -path '*.parsed/*')
  total_files=${#_files[@]}
  for f in "${_files[@]}"; do
    case "${f,,}" in
      *.txt|*.md|*.pdf) supported_count=$(( supported_count + 1 )) ;;
      *)                unsupported_count=$(( unsupported_count + 1 )) ;;
    esac
  done
fi
log "data/inputs/: ${total_files} file(s) — ${supported_count} supported (.txt/.md/.pdf), ${unsupported_count} unsupported"
if (( supported_count == 0 )); then
  warn "no supported documents in data/inputs/ — nothing to ingest"
fi

# --- Trigger the scan -----------------------------------------------------
log "requesting POST /documents/scan ..."
scan="$(curl -fsS -X POST "$DOCS_API/scan" --max-time 30 || die "$EXIT_FAIL" "POST /documents/scan failed")"
scan_status="$(jq -r '.status // empty' <<<"$scan")"
track_id="$(jq -r '.track_id // empty' <<<"$scan")"

if [[ "$scan_status" == "scanning_skipped_pipeline_busy" ]]; then
  die "$EXIT_FAIL" "another scan or indexing run is already in progress — retry when the pipeline is idle"
fi
[[ -n "$track_id" ]] || die "$EXIT_FAIL" "scan did not return a track_id (response: $scan)"
log "scan started; track_id=${track_id}"

# --- Phase 1: wait for the scan's classification phase to finish ----------
# GET /documents/scan/status/{id}.status is "running" until classification is
# done, then "completed" | "failed" | "cancelled" | "abandoned".
start_ts="$(date +%s)"
scan_msg=""
while :; do
  ss="$(http_get "$DOCS_API/scan/status/${track_id}" 2>/dev/null || true)"
  [[ -n "$ss" ]] || ss='{}'
  ss_status="$(jq -r '.status // "running"' <<<"$ss" 2>/dev/null || echo running)"
  case "$ss_status" in
    completed) scan_msg="$(jq -r '.message // ""' <<<"$ss" 2>/dev/null || true)"; break ;;
    failed|cancelled|abandoned)
      die "$EXIT_FAIL" "the directory scan ended '$ss_status': $(jq -r '.message // "no detail"' <<<"$ss" 2>/dev/null)" ;;
    *) : ;;  # running (or endpoint briefly unavailable) — keep waiting
  esac
  (( $(date +%s) - start_ts >= wait_timeout )) && die "$EXIT_FAIL" "timed out waiting for the directory scan to finish"
  sleep 2
done
[[ -n "$scan_msg" ]] && log "scan: $scan_msg"

# --- Phase 2: wait for every enqueued document to reach a terminal state --
# Read .documents[].status directly — those are the clean DocStatus values
# (pending/parsing/analyzing/processing/preprocessed = in progress;
#  processed/failed = terminal). status_summary keys are "DocStatus.X" and are
# not used here.
processed=0 failed=0 enqueued=0 nonterminal=0
while :; do
  ts="$(http_get "$DOCS_API/track_status/${track_id}" 2>/dev/null || true)"
  [[ -n "$ts" ]] || ts='{}'
  read -r enqueued processed failed nonterminal < <(jq -r '
    ([.documents[]?.status // "" | ascii_downcase]) as $s
    | [ (.total_count // ($s | length)),
        ($s | map(select(. == "processed")) | length),
        ($s | map(select(. == "failed"))    | length),
        ($s | map(select(. == "pending" or . == "parsing" or . == "analyzing"
                         or . == "processing" or . == "preprocessed")) | length) ]
    | @tsv' <<<"$ts" 2>/dev/null || printf '0\t0\t0\t0')
  enqueued="${enqueued:-0}"; processed="${processed:-0}"; failed="${failed:-0}"; nonterminal="${nonterminal:-0}"

  busy="$(jq -r '.busy // false' <<<"$(http_get "$DOCS_API/pipeline_status" 2>/dev/null || echo '{}')" 2>/dev/null || echo false)"

  if (( nonterminal == 0 )) && [[ "$busy" != "true" ]]; then
    break
  fi
  log "  ... ${processed} processed / ${failed} failed / ${nonterminal} in progress (enqueued ${enqueued})"
  (( $(date +%s) - start_ts >= wait_timeout )) && {
    err "timed out after ${wait_timeout}s waiting for ingestion to finish"
    err "check progress with: ./scripts/logs.sh   or   curl -s $DOCS_API/track_status/${track_id} | jq"
    exit "$EXIT_FAIL"
  }
  sleep "$POLL_INTERVAL"
done

# --- Summary ------------------------------------------------------------
# `enqueued` (track total_count) = files the server accepted as new/changed this
# run. Supported files NOT enqueued are unchanged since last ingestion (FR-005,
# SC-006). Unsupported files were never candidates (FR-004).
skipped_unchanged=$(( supported_count - enqueued ))
(( skipped_unchanged < 0 )) && skipped_unchanged=0
log "-------------------------------------------"
log "ingestion complete for track ${track_id}"
printf '%s processed / %s skipped (unchanged) / %s skipped (unsupported) / %s failed\n' \
  "$processed" "$skipped_unchanged" "$unsupported_count" "$failed"

# Stamp what embedding model/dimension the index was last built against, so
# health.sh / restore.sh can warn when .env drifts and a re-index is due (FR-025).
if command -v jq >/dev/null 2>&1; then
  stamp="$REPO_ROOT/data/rag_storage/.literag-index.json"
  jq -nc --arg m "${EMBEDDING_MODEL:-}" --arg d "${EMBEDDING_DIM:-}" \
     --arg t "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg tr "$track_id" \
     '{embedding_model:$m, embedding_dim:$d, updated_utc:$t, last_track_id:$tr}' \
     > "$stamp" 2>/dev/null || true
fi

if (( failed > 0 )); then
  # Per-document failure detail (FR-022): name the document, the stage, the reason.
  # The stage is taken from the error message, which LightRAG prefixes with the
  # failing phase (e.g. "[File Extraction] ...", "[Entity Extraction] ...").
  err "${failed} document(s) failed:"
  final="$(http_get "$DOCS_API/track_status/${track_id}" 2>/dev/null || echo '{}')"
  while IFS=$'\t' read -r fpath emsg csum; do
    [[ -z "$fpath$emsg" ]] && continue
    stage="$(sed -n 's/^\[\([^]]*\)\].*/\1/p' <<<"$emsg")"
    [[ -n "$stage" ]] || stage="unknown stage"
    err "  - ${fpath:-<unknown file>}"
    err "      stage:  ${stage}"
    err "      reason: ${emsg:-<no message>}"
    [[ -n "$csum" && "$csum" != "null" ]] && err "      doc:    ${csum}"
  done < <(jq -r '
    .documents // []
    | map(select((.status // "" | ascii_downcase) == "failed"))
    | .[] | [ (.file_path // .id // ""), (.error_msg // ""), (.content_summary // "") ] | @tsv
  ' <<<"$final" 2>/dev/null)
  err ""
  err "Documents that processed in this run remain valid — the batch was not rolled back."
  err "Fix the source and re-run ./scripts/ingest.sh (see docs/TROUBLESHOOTING.md)."
  exit "$EXIT_FAIL"
fi
exit "$EXIT_OK"
