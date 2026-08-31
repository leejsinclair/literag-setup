#!/usr/bin/env bash
# scripts/apply-perf-tuning.sh — one-shot migration to the GPU-tuned profile.
#
# What it does (in order), stopping at the first failure:
#   1. installs ollama/ollama.service as a systemd USER unit
#   2. stops any manually-started `ollama serve` and starts the tuned service
#   3. warms qwen2.5:3b + bge-m3 and checks `ollama ps` shows "100% GPU"
#   4. recreates the LightRAG container so it picks up the new .env
#   5. runs health.sh and a small ingest timing probe
#
#   0  everything applied and verified
#   1  a step failed (message says which; Ollama is rolled back to `ollama serve`)
#   3  unmet prerequisite
#
# Safe to re-run. Does NOT need sudo on this box (user systemd + linger already on).
# Rollback by hand:  systemctl --user disable --now ollama && ollama serve &

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

OLLAMA_BIN="$(command -v ollama || true)"
UNIT_SRC="$REPO_ROOT/ollama/ollama.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/ollama.service"
API="http://127.0.0.1:11434"

rolled_back=0
rollback_ollama() {
  (( rolled_back )) && return 0
  rolled_back=1
  warn "rolling Ollama back to a plain 'ollama serve' ..."
  systemctl --user disable --now ollama >/dev/null 2>&1 || true
  ( setsid "$OLLAMA_BIN" serve >/dev/null 2>&1 & ) || true
  sleep 2
  curl -fsS -o /dev/null --max-time 5 "$API/api/version" \
    && log "  plain ollama serve is back up" \
    || err "  could not restart 'ollama serve' — start it manually"
}

# --- 1. preflight --------------------------------------------------------
[[ -n "$OLLAMA_BIN" ]] || die "$EXIT_PREREQ" "ollama not on PATH"
[[ -f "$UNIT_SRC" ]]   || die "$EXIT_PREREQ" "missing $UNIT_SRC"
command -v systemctl >/dev/null || die "$EXIT_PREREQ" "systemctl not found"
command -v nvidia-smi >/dev/null || die "$EXIT_PREREQ" \
  "nvidia-smi not found — this profile assumes an NVIDIA GPU. Revert .env from git if this box has no GPU."
require_docker_daemon

log "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
grep -q '^OLLAMA_LLM_NUM_GPU=99' "$ENV_FILE" \
  || die "$EXIT_PREREQ" ".env is not the tuned profile (OLLAMA_LLM_NUM_GPU=99 missing). Pull latest and retry."

# --- 2. install + start the user service --------------------------------
log "installing $UNIT_DST ..."
mkdir -p "$(dirname "$UNIT_DST")"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"

if [[ "$(readlink -f "$OLLAMA_BIN")" != "$HOME/.local/bin/ollama" ]]; then
  warn "ollama is at $OLLAMA_BIN, not ~/.local/bin/ollama — patching ExecStart in the installed unit"
  sed -i "s#ExecStart=%h/.local/bin/ollama serve#ExecStart=$OLLAMA_BIN serve#" "$UNIT_DST"
fi

log "stopping any manually-started 'ollama serve' ..."
systemctl --user stop ollama >/dev/null 2>&1 || true
pkill -x ollama 2>/dev/null || true
sleep 2

log "starting the tuned service ..."
systemctl --user daemon-reload
systemctl --user enable --now ollama
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

for _ in $(seq 1 20); do
  curl -fsS -o /dev/null --max-time 3 "$API/api/version" && break
  sleep 1
done
if ! curl -fsS -o /dev/null --max-time 3 "$API/api/version"; then
  err "the tuned Ollama service did not come up:"
  systemctl --user status ollama --no-pager -l | tail -20 >&2 || true
  rollback_ollama
  die "$EXIT_FAIL" "Ollama service failed to start"
fi
log "service is up. Environment:"
systemctl --user show ollama -p Environment | tr ' ' '\n' | sed 's/^/  /' >&2

# --- 3. warm the models and check GPU placement ------------------------
log "warming qwen2.5:3b-instruct and bge-m3 (forced full GPU) ..."
curl -fsS "$API/api/generate" -d '{"model":"qwen2.5:3b-instruct","prompt":"ok","stream":false,"keep_alive":"30m","options":{"num_gpu":99,"num_ctx":12288}}' -o /dev/null
curl -fsS "$API/api/embed"    -d '{"model":"bge-m3:latest","input":"ok","keep_alive":"30m","options":{"num_gpu":99,"num_ctx":2048}}' -o /dev/null

ps_out="$("$OLLAMA_BIN" ps)"
printf '%s\n' "$ps_out" | sed 's/^/  /' >&2
if printf '%s\n' "$ps_out" | grep -qiE '[0-9]+%/[0-9]+% +CPU/GPU|100% CPU'; then
  warn "a model is NOT fully on the GPU (see PROCESSOR column above)."
  warn "  → lower OLLAMA_LLM_NUM_CTX / OLLAMA_EMBEDDING_NUM_CTX in .env, then re-run."
fi
# Both models must stay resident together. If only one is listed after warming
# both, they are evicting each other — OLLAMA_NUM_PARALLEL must be 1 (it is, in
# the unit file) and the contexts must be small enough to co-reside.
if [[ "$(printf '%s\n' "$ps_out" | grep -cE 'qwen2\.5:3b|bge-m3' || true)" -lt 2 ]]; then
  warn "only one model stayed resident after warming both — they are thrashing."
  warn "  → check OLLAMA_NUM_PARALLEL=1 in $UNIT_DST and lower the *_NUM_CTX values in .env."
fi
vram="$(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader)"
log "VRAM: $vram"

# --- 4. recreate the LightRAG container --------------------------------
log "recreating the LightRAG container to load the new .env ..."
"$REPO_ROOT/scripts/restart.sh" --recreate || die "$EXIT_FAIL" "LightRAG did not become healthy — see logs above"

# --- 5. verify --------------------------------------------------------
log "running health.sh ..."
"$REPO_ROOT/scripts/health.sh" || warn "health.sh reported an issue — inspect above"

cat >&2 <<EOF

--------------------------------------------------------------------------
Applied. Next, verify the speedup yourself:

  # ingest timing (fixtures) — compare to the ~30-60s/doc baseline
  time ./scripts/ingest.sh

  # full end-to-end contract check (backs up + restores your KB)
  ./scripts/smoke-test.sh

  # watch GPU load during an ingest, in another terminal
  nvidia-smi dmon -s u

  # a fast query (local mode is much cheaper than the hybrid default)
  curl -s localhost:${PORT}/query -H 'content-type: application/json' \\
    -d '{"query":"<your question>","mode":"local"}' | jq '.response_time, .response'

Rollback if needed:
  git checkout -- .env .env.example
  systemctl --user disable --now ollama
  nohup ollama serve >/dev/null 2>&1 &
  ./scripts/restart.sh --recreate
--------------------------------------------------------------------------
EOF
log "done."
