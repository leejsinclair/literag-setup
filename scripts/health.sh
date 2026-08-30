#!/usr/bin/env bash
# scripts/health.sh — dependency-verifying health check.
#
# Contract: contracts/operational-cli.md → health.sh  (FR-020, FR-021)
#   Args:   [--json] [--quiet]
#   0  every check passed (warnings allowed)
#   1  at least one check failed
#   3  unmet prerequisite
#
# Checks, each reported individually (overall = worst):
#   1 LightRAG      GET /health -> 200, status healthy
#   2 Ollama up     GET ${LLM_BINDING_HOST}/api/tags -> 200
#   3 Models present LLM_MODEL and EMBEDDING_MODEL appear in /api/tags
#   4 LLM role      minimal POST /api/generate succeeds
#   5 Embedding role minimal POST /api/embed succeeds; vector length == EMBEDDING_DIM
#   6 Storage       create + delete a temp file under data/rag_storage/
#   7 Config sanity HOST == 127.0.0.1 (warn only)
#
# Relies only on the always-present (unauthenticated) /health fields.

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

as_json=0
quiet=0
while (( $# )); do
  case "$1" in
    --json)  as_json=1; shift ;;
    --quiet) quiet=1; shift ;;
    -h|--help) printf 'Usage: health.sh [--json] [--quiet]\n' >&2; exit "$EXIT_OK" ;;
    *) die "$EXIT_USAGE" "unknown argument: $1" ;;
  esac
done

OLLAMA="${LLM_BINDING_HOST%/}"
LR="http://127.0.0.1:${PORT}"

names=(); verdicts=(); details=()
record() { names+=("$1"); verdicts+=("$2"); details+=("$3"); }

# --- 1. LightRAG -------------------------------------------------------
if body="$(curl -fsS --max-time 8 "$LR/health" 2>/dev/null)"; then
  st="$(jq -r '.status // "unknown"' <<<"$body" 2>/dev/null || echo unknown)"
  cv="$(jq -r '.core_version // "?"' <<<"$body" 2>/dev/null || echo '?')"
  if [[ "$st" == "healthy" ]]; then
    record "lightrag" ok "GET /health 200 (status=healthy, core_version=$cv)"
  else
    record "lightrag" fail "GET /health 200 but status='$st' — check ./scripts/logs.sh"
  fi
else
  record "lightrag" fail "GET $LR/health did not return 200 — is the container up? ./scripts/start.sh"
fi

# --- 2. Ollama reachable ---------------------------------------------
tags=""
if tags="$(curl -fsS --max-time 8 "$OLLAMA/api/tags" 2>/dev/null)"; then
  n="$(jq -r '.models | length' <<<"$tags" 2>/dev/null || echo '?')"
  record "ollama" ok "GET $OLLAMA/api/tags 200 ($n model(s) installed)"
else
  record "ollama" fail "cannot reach Ollama at $OLLAMA/api/tags — is 'ollama serve' running? is LLM_BINDING_HOST correct? (see docs/TROUBLESHOOTING.md)"
fi

model_present() {
  local want="$1"
  jq -e --arg m "$want" '
    .models // [] | map(.name) as $names
    | ($names | index($m)) != null
      or ($names | index($m + ":latest")) != null
      or ($names | any(startswith($m + ":")))
      or ($m | endswith(":latest")) and ($names | index($m | sub(":latest$";""))) != null
  ' >/dev/null 2>&1 <<<"$tags"
}

# --- 3. Models present ---------------------------------------------
if [[ -n "$tags" ]]; then
  missing=()
  model_present "${LLM_MODEL:-}"       || missing+=("LLM_MODEL=${LLM_MODEL:-unset}")
  model_present "${EMBEDDING_MODEL:-}" || missing+=("EMBEDDING_MODEL=${EMBEDDING_MODEL:-unset}")
  if (( ${#missing[@]} == 0 )); then
    record "models" ok "LLM_MODEL ($LLM_MODEL) and EMBEDDING_MODEL ($EMBEDDING_MODEL) are installed"
  else
    record "models" fail "not installed: ${missing[*]} — run: ollama pull <model>"
  fi
else
  record "models" fail "skipped — Ollama unreachable"
fi

# --- 4. LLM role -------------------------------------------------
if [[ -n "$tags" ]] && model_present "${LLM_MODEL:-}"; then
  if curl -fsS --max-time 60 "$OLLAMA/api/generate" \
        -d "$(jq -nc --arg m "$LLM_MODEL" '{model:$m, prompt:"ping", stream:false}')" \
        >/dev/null 2>&1; then
    record "llm-role" ok "POST /api/generate with $LLM_MODEL succeeded"
  else
    record "llm-role" fail "POST /api/generate with $LLM_MODEL failed — model loads but does not generate (out of memory? corrupt pull?)"
  fi
else
  record "llm-role" fail "skipped — $LLM_MODEL not available"
fi

# --- 5. Embedding role + dimension --------------------------------
if [[ -n "$tags" ]] && model_present "${EMBEDDING_MODEL:-}"; then
  emb="$(curl -fsS --max-time 60 "$OLLAMA/api/embed" \
        -d "$(jq -nc --arg m "$EMBEDDING_MODEL" '{model:$m, input:"ping"}')" 2>/dev/null || true)"
  [[ -n "$emb" ]] || emb='{}'
  dim="$(jq -r 'first((.embeddings[0] // .embedding // []) | length) // 0' <<<"$emb" 2>/dev/null || echo 0)"
  if [[ "$dim" == "0" || -z "$dim" ]]; then
    record "embedding-role" fail "POST /api/embed with $EMBEDDING_MODEL returned no vector"
  elif [[ -n "${EMBEDDING_DIM:-}" && "$dim" != "$EMBEDDING_DIM" ]]; then
    record "embedding-role" fail "DIMENSION MISMATCH: $EMBEDDING_MODEL returns $dim but EMBEDDING_DIM=$EMBEDDING_DIM — fix EMBEDDING_DIM in .env (Invariant 3); a wrong value corrupts the vector index"
  else
    record "embedding-role" ok "POST /api/embed with $EMBEDDING_MODEL returned a $dim-dim vector (matches EMBEDDING_DIM)"
  fi
else
  record "embedding-role" fail "skipped — $EMBEDDING_MODEL not available"
fi

# --- 6. Storage writable ------------------------------------------
kb="$REPO_ROOT/data/rag_storage"
probe="$kb/.health_write_probe.$$"
if [[ -d "$kb" ]] && ( : > "$probe" ) 2>/dev/null && rm -f "$probe" 2>/dev/null; then
  record "storage" ok "data/rag_storage/ is writable"
else
  record "storage" fail "cannot write to data/rag_storage/ — check directory ownership/permissions (must be writable by your user)"
fi

# --- 6b. Index freshness vs .env embedding settings (FR-025) --------
stamp="$kb/.literag-index.json"
if [[ -f "$stamp" ]]; then
  s_model="$(jq -r '.embedding_model // ""' "$stamp" 2>/dev/null || echo '')"
  s_dim="$(jq -r '.embedding_dim // ""' "$stamp" 2>/dev/null || echo '')"
  if [[ ( -n "$s_model" && "$s_model" != "${EMBEDDING_MODEL:-}" ) || ( -n "$s_dim" && "$s_dim" != "${EMBEDDING_DIM:-}" ) ]]; then
    record "index-freshness" fail "the vector index was built with embedding_model=$s_model dim=$s_dim but .env now has ${EMBEDDING_MODEL:-?}/${EMBEDDING_DIM:-?} — RE-INDEX REQUIRED (docs/MODEL_SELECTION.md); stale results must not be treated as current"
  else
    record "index-freshness" ok "vector index matches .env embedding settings ($s_model / $s_dim)"
  fi
else
  record "index-freshness" ok "no index stamp yet (nothing ingested) — nothing to compare"
fi

# --- 7. Config sanity -------------------------------------------
if [[ "${HOST:-}" == "127.0.0.1" ]]; then
  record "config" ok "HOST=127.0.0.1 (loopback-only, as required)"
else
  record "config" warn "HOST='${HOST:-unset}' is NOT 127.0.0.1 — the server may be exposed off-host. Set LIGHTRAG_API_KEY before doing this deliberately (FR-028a)."
fi

# --- Verdict ----------------------------------------------------
overall="HEALTHY"; exit_code="$EXIT_OK"
for v in "${verdicts[@]}"; do
  [[ "$v" == "warn" && "$overall" == "HEALTHY" ]] && overall="HEALTHY (with warnings)"
  if [[ "$v" == "fail" ]]; then overall="UNHEALTHY"; exit_code="$EXIT_FAIL"; fi
done

if (( as_json )); then
  entries=""
  for i in "${!names[@]}"; do
    entries+="$(jq -nc --arg n "${names[$i]}" --arg v "${verdicts[$i]}" --arg d "${details[$i]}" '{check:$n,verdict:$v,detail:$d}')"$'\n'
  done
  jq -s --arg overall "$overall" '{overall:$overall, checks:.}' <<<"$entries"
elif (( ! quiet )); then
  for i in "${!names[@]}"; do
    case "${verdicts[$i]}" in
      ok)   sym="  OK  " ;;
      warn) sym=" WARN " ;;
      *)    sym=" FAIL " ;;
    esac
    printf '[%s] %-16s %s\n' "$sym" "${names[$i]}" "${details[$i]}" >&2
  done
  printf '\n%s\n' "$overall" >&2
else
  printf '%s\n' "$overall" >&2
fi

exit "$exit_code"
