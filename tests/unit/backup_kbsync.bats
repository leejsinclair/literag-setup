#!/usr/bin/env bats
# T018 — FR-005 and the "Restore" edge case (spec 002).
#
#   backup.sh archives data/kb-sync/ next to data/rag_storage/, records it in the
#   manifest, and verifies it is in the archive. restore.sh --force extracts it and
#   moves the current copy aside to data/kb-sync.pre-restore-<ts>/ alongside
#   data/rag_storage.pre-restore-<ts>/, never deleting it (data-model.md → Backup
#   artifact; Invariant 7).
#
# Test-first (tasks.md Phase 2, Wave 1): the kb-sync assertions fail until T027
# (backup.sh) and T028 (restore.sh) land.
#
# Runs against a fake repo in $BATS_TEST_TMPDIR with stub `docker` and `curl` first
# on PATH. Never touches the real Docker daemon or the real data/.
#   bats tests/unit/backup_kbsync.bats

setup() {
  REAL_REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  FAKE="$BATS_TEST_TMPDIR/repo"
  STUBS="$BATS_TEST_TMPDIR/bin"
  export FAKE_LOG="$BATS_TEST_TMPDIR/calls.log"
  export FAKE_RUNNING=0
  export FAKE_HEALTH=ok
  : > "$FAKE_LOG"

  mkdir -p "$FAKE" "$STUBS"
  cp -R "$REAL_REPO/scripts" "$FAKE/scripts"
  : > "$FAKE/compose.yaml"
  cat > "$FAKE/.env" <<'EOF'
HOST=127.0.0.1
PORT=9621
HEALTH_TIMEOUT_SECONDS=1
BACKUP_RETENTION=14
LIGHTRAG_IMAGE_TAG=v1.5.6
LLM_MODEL=qwen2.5:3b-instruct
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768
EOF

  # docker stub: logs every call, answers from FAKE_* variables.
  cat > "$STUBS/docker" <<'EOF'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$FAKE_LOG"
args=" $* "
case "$args" in
  *" compose version "*) echo "Docker Compose version v2.99.0"; exit 0 ;;
  " info "*) exit 0 ;;
  *" ps -q "*) [[ "${FAKE_RUNNING:-0}" == 1 ]] && echo fakecontainerid; exit 0 ;;
  *" inspect "*State.Running*) [[ "${FAKE_RUNNING:-0}" == 1 ]] && echo true || echo false; exit 0 ;;
  *" inspect "*) exit 1 ;;
  *) exit 0 ;;
esac
EOF
  # curl stub: /health answers per FAKE_HEALTH; anything else fails.
  cat > "$STUBS/curl" <<'EOF'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  */health*) [[ "${FAKE_HEALTH:-ok}" == ok ]] || exit 7
             echo '{"status":"healthy","core_version":"v1.5.6","api_version":"0246"}'; exit 0 ;;
  *) exit 7 ;;
esac
EOF
  chmod +x "$STUBS/docker" "$STUBS/curl"
  export PATH="$STUBS:$PATH"

  # A small knowledge base and kb-sync records tree.
  mkdir -p "$FAKE/data/rag_storage" "$FAKE/data/kb-sync/runs" "$FAKE/data/inputs"
  echo '{"graph":"old"}' > "$FAKE/data/rag_storage/kv_store_doc_status.json"
  echo 'records-v1' > "$FAKE/data/kb-sync/kb-sync.db"
  echo '{"run_id":"r1"}' > "$FAKE/data/kb-sync/runs/r1.json"
}

# Runs backup.sh in the fake repo; sets $archive to its stdout's last line.
make_backup() {
  run "$FAKE/scripts/backup.sh" "$@"
  [ "$status" -eq 0 ] || { echo "backup.sh failed ($status): $output" >&2; return 1; }
  archive="$(printf '%s\n' "$output" | tail -n 1)"
  [ -f "$archive" ]
}

@test "backup.sh archives data/kb-sync/ next to data/rag_storage/" {
  make_backup
  listing="$(tar -tzf "$archive")"
  grep -q '^\(\./\)\?data/rag_storage/kv_store_doc_status.json$' <<<"$listing"
  grep -q '^\(\./\)\?data/kb-sync/kb-sync.db$' <<<"$listing"
  grep -q '^\(\./\)\?data/kb-sync/runs/r1.json$' <<<"$listing"
}

@test "backup.sh records kb-sync in the manifest" {
  make_backup
  manifest="${archive%.tar.gz}.manifest"
  [ -f "$manifest" ]
  grep -q '^kb_sync_records=true$' "$manifest"
  grep -q '^kb_sync_schema_version=' "$manifest"
}

@test "backup.sh leaves data/kb-sync/ in place and unchanged" {
  before="$(cd "$FAKE/data/kb-sync" && find . -type f -exec sha256sum {} + | sort)"
  make_backup
  after="$(cd "$FAKE/data/kb-sync" && find . -type f -exec sha256sum {} + | sort)"
  [ "$before" = "$after" ]
}

@test "backup.sh fails verification and keeps no archive when data/kb-sync/ is missing from it" {
  # A tar that silently drops data/kb-sync/ on create; the verify step must catch it.
  cat > "$STUBS/tar" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do
  if [[ "$a" == -c* ]]; then exec /usr/bin/tar --exclude=data/kb-sync "$@"; fi
done
exec /usr/bin/tar "$@"
EOF
  chmod +x "$STUBS/tar"
  run "$FAKE/scripts/backup.sh"
  [ "$status" -eq 1 ]
  [[ "$output" == *kb-sync* ]]
  run bash -c "ls '$FAKE/data/backups'/kb-*.tar.gz 2>/dev/null"
  [ -z "$output" ]
  # The live records are untouched by the failed backup.
  [ "$(cat "$FAKE/data/kb-sync/kb-sync.db")" = "records-v1" ]
}

@test "restore.sh --force extracts data/kb-sync/ and moves the current copy aside with data/rag_storage/" {
  make_backup
  # The live state moves on after the backup.
  echo 'records-v2' > "$FAKE/data/kb-sync/kb-sync.db"
  echo '{"run_id":"r2"}' > "$FAKE/data/kb-sync/runs/r2.json"
  echo '{"graph":"new"}' > "$FAKE/data/rag_storage/kv_store_doc_status.json"

  run "$FAKE/scripts/restore.sh" "$archive" --force --assume-yes < /dev/null
  [ "$status" -eq 0 ]

  # Restored records match the backup.
  [ "$(cat "$FAKE/data/kb-sync/kb-sync.db")" = "records-v1" ]
  [ -f "$FAKE/data/kb-sync/runs/r1.json" ]
  [ ! -e "$FAKE/data/kb-sync/runs/r2.json" ]
  [ "$(cat "$FAKE/data/rag_storage/kv_store_doc_status.json")" = '{"graph":"old"}' ]

  # The previous copies were moved aside, never deleted, under one shared timestamp.
  kb_aside=( "$FAKE"/data/rag_storage.pre-restore-* )
  rec_aside=( "$FAKE"/data/kb-sync.pre-restore-* )
  [ "${#kb_aside[@]}" -eq 1 ] && [ -d "${kb_aside[0]}" ]
  [ "${#rec_aside[@]}" -eq 1 ] && [ -d "${rec_aside[0]}" ]
  [ "${kb_aside[0]##*pre-restore-}" = "${rec_aside[0]##*pre-restore-}" ]
  [ "$(cat "${rec_aside[0]}/kb-sync.db")" = "records-v2" ]
  [ -f "${rec_aside[0]}/runs/r2.json" ]
  [ "$(cat "${kb_aside[0]}/kv_store_doc_status.json")" = '{"graph":"new"}' ]
}

@test "restore.sh without --force refuses and leaves data/kb-sync/ untouched" {
  make_backup
  echo 'records-v2' > "$FAKE/data/kb-sync/kb-sync.db"
  run "$FAKE/scripts/restore.sh" "$archive" < /dev/null
  [ "$status" -eq 4 ]
  [ "$(cat "$FAKE/data/kb-sync/kb-sync.db")" = "records-v2" ]
  run bash -c "ls -d '$FAKE'/data/kb-sync.pre-restore-* 2>/dev/null"
  [ -z "$output" ]
}

@test "restore.sh of a pre-002 archive without data/kb-sync/ restores the KB, warns, and deletes no records" {
  old="$BATS_TEST_TMPDIR/kb-20260101T000000Z.tar.gz"
  tar -czf "$old" -C "$FAKE" data/rag_storage
  echo 'records-v2' > "$FAKE/data/kb-sync/kb-sync.db"
  echo '{"graph":"new"}' > "$FAKE/data/rag_storage/kv_store_doc_status.json"

  run "$FAKE/scripts/restore.sh" "$old" --force --assume-yes < /dev/null
  [ "$status" -eq 0 ]
  [ "$(cat "$FAKE/data/rag_storage/kv_store_doc_status.json")" = '{"graph":"old"}' ]
  [[ "$output" == *WARNING:*kb-sync* || "$output" == *WARNING:*records* ]]
  # The current records survive somewhere: in place or moved aside, never deleted.
  run bash -c "grep -rl 'records-v2' '$FAKE'/data/kb-sync '$FAKE'/data/kb-sync.pre-restore-* 2>/dev/null"
  [ -n "$output" ]
}
