#!/usr/bin/env bash
# scripts/smoke-test.sh — end-to-end validation (FR-031, SC-011, SC-012).
#
# Contract: contracts/lightrag-api.md → "Contract tests C1–C8"
#   Args:   [--keep]   leave the fixture state in place; skip pre-test restore
#   0  all checks passed  (prints a PASS/FAIL table)
#   1  a check failed     (stops at the first failure; system left inspectable)
#   3  unmet prerequisite
#
# Runs the real pipeline against the real CPU model. By default it takes a full
# backup first and restores the pre-test data/inputs/ + data/rag_storage/ on exit
# so the run is non-destructive to your existing knowledge base.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

keep=0
case "${1:-}" in
  --keep) keep=1 ;;
  "") ;;
  -h|--help) printf 'Usage: smoke-test.sh [--keep]\n' >&2; exit "$EXIT_OK" ;;
  *) die "$EXIT_USAGE" "unknown argument: $1" ;;
esac

require_docker_daemon

LR="http://127.0.0.1:${PORT}"
DOCS="$LR/documents"
FIX="$REPO_ROOT/tests/fixtures"
INPUTS="$REPO_ROOT/data/inputs"
KB="$REPO_ROOT/data/rag_storage"

[[ -f "$FIX/sample.md" && -f "$FIX/sample.pdf" ]] || die "$EXIT_PREREQ" "missing fixtures in tests/fixtures/ (run: python3 tests/fixtures/make_sample_pdf.py)"

# Distinctive facts that appear ONLY in the fixtures.
MD_QUERY='In what city and on what date was the Verdant Sluicegate Protocol ratified?'
MD_EXPECT='quillhaven'
MD_EXPECT2='1987'
PDF_QUERY='How many brass astrolabes were in shipment QK-4417 and who dispatched them?'
PDF_EXPECT='1,204'
PDF_EXPECT_ALT='1204'
NOINFO_QUERY='What does this knowledge base say about the annual rainfall in the city of Ombros?'

results=()   # "Cn|PASS/FAIL/SKIP|note"
row() { results+=("$1|$2|$3"); }

print_table() {
  printf '\n%-4s %-6s %s\n' "CHK" "RESULT" "NOTE" >&2
  printf '%-4s %-6s %s\n' "---" "------" "----" >&2
  local r
  for r in "${results[@]}"; do
    IFS='|' read -r c v n <<<"$r"
    printf '%-4s %-6s %s\n' "$c" "$v" "$n" >&2
  done
}

fail() { # <check> <note>
  row "$1" FAIL "$2"
  print_table
  err ""
  err "$1 FAILED: $2"
  if (( keep )); then
    err "--keep: fixture state left in place; pre-test backup: ${PRETEST_ARCHIVE:-<none>}"
  else
    err "pre-test data/inputs/ + data/rag_storage/ will now be restored (see below)."
  fi
  exit "$EXIT_FAIL"
}

# --- pre-test state capture ------------------------------------------
PRETEST_ARCHIVE=""
cleanup() {
  local rc=$?
  if (( keep )); then
    log "--keep: leaving fixture state in place. Pre-test backup: ${PRETEST_ARCHIVE:-<none>}"
    return
  fi
  [[ -n "$PRETEST_ARCHIVE" && -f "$PRETEST_ARCHIVE" ]] || return
  log "restoring pre-test state ..."
  compose stop >/dev/null 2>&1 || true
  rm -rf "$KB" "$INPUTS"
  mkdir -p "$KB" "$INPUTS"
  tar -xzf "$PRETEST_ARCHIVE" -C "$REPO_ROOT" data/rag_storage data/inputs 2>/dev/null || true
  rm -rf "$REPO_ROOT"/data/rag_storage.pre-restore-* 2>/dev/null || true
  rm -f "$PRETEST_ARCHIVE" "${PRETEST_ARCHIVE%.tar.gz}.manifest" 2>/dev/null || true
  compose up -d >/dev/null 2>&1 || true
  log "pre-test state restored (rc=$rc)"
}
trap cleanup EXIT

log "taking a pre-test backup (data/inputs/ + data/rag_storage/) ..."
PRETEST_ARCHIVE="$("$REPO_ROOT/scripts/backup.sh" --with-inputs)" \
  || die "$EXIT_FAIL" "pre-test backup failed — aborting before touching anything"
log "pre-test backup: $PRETEST_ARCHIVE"

# stage fixtures as the only inputs
rm -rf "$INPUTS"; mkdir -p "$INPUTS"
cp "$FIX/sample.md" "$FIX/sample.pdf" "$INPUTS/"
# start from an empty KB so C4/C6 counts are unambiguous
compose stop >/dev/null 2>&1 || true
rm -rf "$KB"; mkdir -p "$KB"; : > "$KB/.gitkeep"

# ============================ C1 ====================================
log "C1: start + /health ..."
if "$REPO_ROOT/scripts/start.sh" >/dev/null 2>&1 && wait_for_health "${HEALTH_TIMEOUT_SECONDS}"; then
  row C1 PASS "GET /health 200 after start.sh"
else
  fail C1 "service did not become healthy after start.sh"
fi

# ============================ C2 ====================================
log "C2: Web UI reachable ..."
# GET / 307-redirects to /webui/ in current LightRAG; follow it.
code="$(curl -sL -o /tmp/smoke_root.$$ -w '%{http_code}' "$LR/" || echo 000)"
if [[ "$code" == "200" ]] && grep -qi '<html\|<!doctype html' /tmp/smoke_root.$$; then
  row C2 PASS "GET / -> /webui/ 200 + HTML"
else
  rm -f /tmp/smoke_root.$$; fail C2 "Web UI not reachable: GET / returned $code / not HTML"
fi
rm -f /tmp/smoke_root.$$

# ============================ C3 ====================================
log "C3: API docs reachable ..."
code="$(curl -s -o /dev/null -w '%{http_code}' "$LR/docs" || echo 000)"
[[ "$code" == "200" ]] && row C3 PASS "GET /docs 200" || fail C3 "GET /docs returned $code"

# ============================ C4 ====================================
log "C4: ingest .md + .pdf fixtures ..."
if "$REPO_ROOT/scripts/ingest.sh" --wait-timeout 3000 >/tmp/smoke_ingest.$$ 2>&1; then
  counts="$(curl -fsS "$DOCS/status_counts" | jq -r '(.status_counts // {}) | to_entries | map("\(.key)=\(.value)") | join(" ")')"
  proc="$(curl -fsS "$DOCS/status_counts" | jq -r '(.status_counts.processed // .status_counts.PROCESSED // 0)')"
  if [[ "$proc" == "2" ]]; then
    row C4 PASS "both fixtures processed ($counts)"
  else
    cat /tmp/smoke_ingest.$$ >&2; rm -f /tmp/smoke_ingest.$$
    fail C4 "expected 2 processed, got: $counts"
  fi
else
  cat /tmp/smoke_ingest.$$ >&2; rm -f /tmp/smoke_ingest.$$
  fail C4 "ingest.sh exited non-zero"
fi
rm -f /tmp/smoke_ingest.$$

# ============================ C5 ====================================
log "C5: query returns fixture facts + source references ..."
# query <question>  -> prints "<refcount><TAB><lowercased single-line response>"
query() {
  local q resp
  q="$(jq -nc --arg q "$1" '{query:$q, mode:"hybrid"}')"
  resp="$(curl -fsS -X POST "$LR/query" -H 'Content-Type: application/json' -d "$q" 2>/dev/null || echo '{}')"
  [[ -n "$resp" ]] || resp='{}'
  jq -r '"\((.references // []) | length)\t\((.response // "") | ascii_downcase | gsub("\\s+";" "))"' \
     <<<"$resp" 2>/dev/null || printf '0\t'
}

IFS=$'\t' read -r qrefs ans < <(query "$MD_QUERY")
if [[ "$ans" == *"$MD_EXPECT"* && "$ans" == *"$MD_EXPECT2"* && "${qrefs:-0}" -ge 1 ]]; then
  row C5a PASS "markdown fact retrieved with ${qrefs} reference(s)"
else
  fail C5a "markdown answer missing '$MD_EXPECT'/'$MD_EXPECT2' or no refs (refs=${qrefs:-0}): ${ans:0:200}"
fi

IFS=$'\t' read -r qrefs ans < <(query "$PDF_QUERY")
if [[ ( "$ans" == *"$PDF_EXPECT"* || "$ans" == *"$PDF_EXPECT_ALT"* ) && "${qrefs:-0}" -ge 1 ]]; then
  row C5b PASS "PDF fact retrieved with ${qrefs} reference(s)"
else
  fail C5b "PDF answer missing '$PDF_EXPECT' or no refs (refs=${qrefs:-0}): ${ans:0:200}"
fi

IFS=$'\t' read -r qrefs ans < <(query "$NOINFO_QUERY")
if [[ "$ans" == *"quillhaven"* || "$ans" == *"astrolabe"* ]]; then
  fail C5c "no-info query leaked unrelated fixture content: ${ans:0:200}"
else
  row C5c PASS "no-info query did not fabricate fixture-sourced content"
fi

# ============================ C6 ====================================
log "C6: survive container recreation, no reprocessing ..."
"$REPO_ROOT/scripts/restart.sh" --recreate >/dev/null 2>&1 || fail C6 "restart --recreate failed"
wait_for_health "${HEALTH_TIMEOUT_SECONDS}" || fail C6 "unhealthy after recreation"
proc="$(curl -fsS "$DOCS/status_counts" | jq -r '(.status_counts.processed // .status_counts.PROCESSED // 0)')"
[[ "$proc" == "2" ]] || fail C6 "after recreation, processed count = $proc (expected 2)"
scan="$(curl -fsS -X POST "$DOCS/scan")"
tid="$(jq -r '.track_id' <<<"$scan")"
sleep 8
enq="$(curl -fsS "$DOCS/track_status/$tid" | jq -r '.total_count // 0')"
IFS=$'\t' read -r qrefs ans < <(query "$MD_QUERY")
if [[ "$ans" == *"$MD_EXPECT"* && "$enq" == "0" ]]; then
  row C6 PASS "same fact after recreation; re-scan enqueued 0"
else
  fail C6 "post-recreation query missing fact, or re-scan enqueued $enq (expected 0)"
fi

# ============================ C7 ====================================
log "C7: not reachable off-loopback ..."
lan_ip="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)"
if [[ -z "$lan_ip" ]]; then
  row C7 SKIP "no non-loopback interface on this machine"
else
  if curl -sS --max-time 5 -o /dev/null "http://$lan_ip:${PORT}/health" 2>/dev/null; then
    fail C7 "service answered on $lan_ip:${PORT} — it is NOT loopback-only (check HOST in .env)"
  else
    row C7 PASS "no response on $lan_ip:${PORT} (loopback-only confirmed)"
  fi
fi

# ============================ C8 ====================================
log "C8: backup -> wipe -> restore -> same fact ..."
c8_archive="$("$REPO_ROOT/scripts/backup.sh")" || fail C8 "backup.sh failed"
compose stop >/dev/null 2>&1 || true
rm -rf "$KB"; mkdir -p "$KB"; : > "$KB/.gitkeep"
if "$REPO_ROOT/scripts/restore.sh" "$c8_archive" --force --assume-yes >/dev/null 2>&1; then
  wait_for_health "${HEALTH_TIMEOUT_SECONDS}" || fail C8 "unhealthy after restore"
  IFS=$'\t' read -r qrefs ans < <(query "$MD_QUERY")
  [[ "$ans" == *"$MD_EXPECT"* ]] && row C8 PASS "fact returns after backup+wipe+restore" \
    || fail C8 "restored KB did not answer the fixture query: ${ans:0:200}"
else
  fail C8 "restore.sh failed"
fi
rm -rf "$REPO_ROOT"/data/rag_storage.pre-restore-* 2>/dev/null || true
rm -f "$c8_archive" "${c8_archive%.tar.gz}.manifest" 2>/dev/null || true

# ============================ done ==================================
print_table
log ""
log "ALL CHECKS PASSED"
exit "$EXIT_OK"
