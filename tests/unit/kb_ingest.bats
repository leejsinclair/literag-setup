#!/usr/bin/env bats
# T017 — FR-034 and SC-014: the `scripts/kb.sh ingest` host sequence never removes
# anything when LightRAG is down.
#
# Contract: specs/002-kb-sync-knowledge-pipeline/contracts/kb-cli.md -> "kb-sync ingest"
#   - server stopped before the run  -> exit 3, names ./scripts/start.sh, backup.sh never runs
#   - LightRAG unhealthy after backup -> exit 3, names ./scripts/start.sh,
#                                        `kb-sync ingest --apply` never runs (zero removals)
#
# Runs against a fake repo in $BATS_TEST_TMPDIR with stub `docker`, `curl` and
# `backup.sh`. Never touches the real Docker daemon or the real data/.
#
#   bats tests/unit/kb_ingest.bats

setup() {
  REAL_REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  FAKE="$BATS_TEST_TMPDIR/repo"
  STUBS="$BATS_TEST_TMPDIR/bin"
  export STATE="$BATS_TEST_TMPDIR/state"
  mkdir -p "$FAKE" "$STUBS" "$STATE"

  cp -R "$REAL_REPO/scripts" "$FAKE/scripts"
  : > "$FAKE/compose.yaml"
  mkdir -p "$FAKE/data/rag_storage" "$FAKE/data/inputs" "$FAKE/data/kb-sync" "$FAKE/data/backups"
  cat > "$FAKE/.env" <<'EOF'
HOST=127.0.0.1
PORT=9621
HEALTH_TIMEOUT_SECONDS=1
BACKUP_RETENTION=14
LLM_MODEL=qwen2.5:3b-instruct
EMBEDDING_MODEL=bge-m3:latest
EMBEDDING_DIM=1024
KB_SYNC_REMOVAL_LIMIT=10
EOF

  # backup.sh stub: records the call, prints the archive path on stdout (like the
  # real one) and, when asked, leaves LightRAG down afterwards.
  cat > "$FAKE/scripts/backup.sh" <<'EOF'
#!/usr/bin/env bash
printf 'backup.sh %s\n' "$*" >> "$STATE/calls.log"
if [[ "${FAKE_BACKUP_KILLS_HEALTH:-0}" == 1 ]]; then
  : > "$STATE/health_down"
  : > "$STATE/stopped"
fi
printf '%s\n' "$(cd "$(dirname "$0")/.." && pwd)/data/backups/kb-20261121T023000Z.tar.gz"
EOF
  chmod +x "$FAKE/scripts/backup.sh"

  # docker stub: answers the calls common.sh and kb.sh make.
  cat > "$STUBS/docker" <<'EOF'
#!/usr/bin/env bash
args="$*"
printf 'docker %s\n' "$args" >> "$STATE/calls.log"
running=0
[[ "${FAKE_RUNNING:-1}" == 1 && ! -e "$STATE/stopped" ]] && running=1
case "$args" in
  info*) exit 0 ;;
  "compose version"*|*" version") echo "Docker Compose version v2.30.0"; exit 0 ;;
  *" ps -q lightrag"*|*" ps -q") (( running )) && echo "fakecontainerid"; exit 0 ;;
  inspect*) (( running )) && echo true || echo false; exit 0 ;;
  *"kb-sync ingest --plan"*)
    echo "planning ..." >&2
    echo "plan_id=20261121T023000Z-3f9a removals=${FAKE_REMOVALS:-2} held=0"
    exit 0 ;;
  *"kb-sync ingest --apply"*)
    echo "applied" >&2
    exit "${FAKE_APPLY_EXIT:-0}" ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$STUBS/docker"

  # curl stub: /health answers 200 unless LightRAG is down.
  cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$STATE/calls.log"
down=0
[[ "${FAKE_HEALTH:-ok}" != ok || -e "$STATE/health_down" ]] && down=1
if [[ "$*" == *"/health"* ]]; then
  (( down )) && exit 7
  echo '{"status":"healthy"}'
  exit 0
fi
(( down )) && exit 7
echo '{}'
exit 0
EOF
  chmod +x "$STUBS/curl"

  : > "$STATE/calls.log"
  export PATH="$STUBS:$PATH"
  unset FAKE_RUNNING FAKE_HEALTH FAKE_REMOVALS FAKE_APPLY_EXIT FAKE_BACKUP_KILLS_HEALTH
}

# `! grep` mid-test does not fail a bats test, so assert absence explicitly.
not_called() {
  if grep -q -- "$1" "$STATE/calls.log"; then
    printf 'unexpected call matching %s:\n' "$1" >&2
    cat "$STATE/calls.log" >&2
    return 1
  fi
}

# --- FR-034 / SC-014: unhealthy after the pre-removal backup ----------------

@test "unhealthy after backup.sh: exits 3" {
  export FAKE_REMOVALS=2 FAKE_BACKUP_KILLS_HEALTH=1
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 3 ]
}

@test "unhealthy after backup.sh: backup ran, kb-sync ingest --apply never runs" {
  export FAKE_REMOVALS=2 FAKE_BACKUP_KILLS_HEALTH=1
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 3 ]
  grep -q '^backup.sh' "$STATE/calls.log"
  not_called 'ingest --apply'
}

@test "unhealthy after backup.sh: message names the archive, says nothing was removed, names ./scripts/start.sh" {
  export FAKE_REMOVALS=2 FAKE_BACKUP_KILLS_HEALTH=1
  run "$FAKE/scripts/kb.sh" ingest
  [[ "$output" == *"ERROR: LightRAG did not come back after the backup kb-20261121T023000Z.tar.gz"* ]]
  [[ "$output" == *"Nothing was removed"* ]]
  [[ "$output" == *"./scripts/start.sh"* ]]
}

@test "unhealthy after backup.sh: no index stamp is written" {
  export FAKE_REMOVALS=2 FAKE_BACKUP_KILLS_HEALTH=1
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 3 ]
  [ ! -e "$FAKE/data/rag_storage/.literag-index.json" ]
}

@test "unhealthy after backup.sh with --confirm-removals: still exits 3 with no --apply" {
  export FAKE_REMOVALS=12 FAKE_BACKUP_KILLS_HEALTH=1
  run "$FAKE/scripts/kb.sh" ingest --confirm-removals
  [ "$status" -eq 3 ]
  not_called 'ingest --apply'
}

# --- Server stopped before the run ------------------------------------------

@test "server stopped before the run: exits 3" {
  export FAKE_RUNNING=0 FAKE_HEALTH=down
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 3 ]
}

@test "server stopped before the run: backup.sh is never called" {
  export FAKE_RUNNING=0 FAKE_HEALTH=down
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 3 ]
  not_called '^backup.sh'
}

@test "server stopped before the run: no kb-sync container is run" {
  export FAKE_RUNNING=0 FAKE_HEALTH=down
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 3 ]
  not_called 'kb-sync ingest'
}

@test "server stopped before the run: error names the URL and ./scripts/start.sh" {
  export FAKE_RUNNING=0 FAKE_HEALTH=down
  run "$FAKE/scripts/kb.sh" ingest
  [[ "$output" == *"ERROR: LightRAG server is not running at http://127.0.0.1:9621. Start it with ./scripts/start.sh"* ]]
}

# --- Controls: the stubs do reach --apply when LightRAG is healthy -----------

@test "control: healthy after backup.sh runs --apply with the backup basename" {
  export FAKE_REMOVALS=2
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 0 ]
  grep -q '^backup.sh' "$STATE/calls.log"
  grep -q -- 'kb-sync ingest --apply 20261121T023000Z-3f9a --backup kb-20261121T023000Z.tar.gz' "$STATE/calls.log"
}

@test "control: no removals means no backup and --apply without --backup" {
  export FAKE_REMOVALS=0
  run "$FAKE/scripts/kb.sh" ingest
  [ "$status" -eq 0 ]
  not_called '^backup.sh'
  grep -q -- 'kb-sync ingest --apply 20261121T023000Z-3f9a' "$STATE/calls.log"
  not_called '--backup'
}
