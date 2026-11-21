#!/usr/bin/env bats
# T016 — backup retention (spec 002 FR-033, SC-013; contracts/kb-cli.md →
# "Change to scripts/backup.sh").
#
#   bats tests/unit/backup_retention.bats
#
# Each test builds a throwaway repo in $BATS_TEST_TMPDIR (a copy of scripts/ plus
# a minimal .env), seeds data/backups/ and data/inputs/<source>/.removed/, and
# runs the copied backup.sh. `docker` is a stub on PATH that reports the LightRAG
# service as stopped, so no container is touched. The real data/ is never read.
#
# Written before the pruning code exists (T027): until then the retention
# assertions fail because backup.sh keeps every archive.

bats_require_minimum_version 1.5.0

setup() {
  REAL_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  FAKE="$BATS_TEST_TMPDIR/repo"
  STUBS="$BATS_TEST_TMPDIR/bin"
  DOCKER_LOG="$BATS_TEST_TMPDIR/docker.log"
  mkdir -p "$FAKE" "$STUBS"
  cp -R "$REAL_ROOT/scripts" "$FAKE/scripts"
  : > "$FAKE/compose.yaml"

  write_env 14

  mkdir -p "$FAKE/data/rag_storage" "$FAKE/data/kb-sync" "$FAKE/data/backups" "$FAKE/data/inputs"
  printf '{"doc":"x"}\n' > "$FAKE/data/rag_storage/kv_store_full_docs.json"
  : > "$FAKE/data/kb-sync/kb-sync.db"

  # docker stub: daemon up, compose v2 present, lightrag not running.
  cat > "$STUBS/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$DOCKER_LOG"
exit 0
EOF
  chmod +x "$STUBS/docker"
  export PATH="$STUBS:$PATH"

  BACKUPS="$FAKE/data/backups"
}

# write_env <BACKUP_RETENTION value | "absent">
write_env() {
  {
    printf 'LIGHTRAG_IMAGE_TAG=v1.5.6\n'
    printf 'HOST=127.0.0.1\n'
    printf 'PORT=9621\n'
    printf 'LLM_MODEL=qwen2.5:3b-instruct\n'
    printf 'EMBEDDING_MODEL=bge-m3:latest\n'
    printf 'EMBEDDING_DIM=1024\n'
    printf 'HEALTH_TIMEOUT_SECONDS=1\n'
    if [[ "$1" != absent ]]; then
      printf 'BACKUP_RETENTION=%s\n' "$1"
    fi
  } > "$FAKE/.env"
}

# seed_archives <dir> <count> <year>: kb-<year>0101T0000<NN>Z.tar.gz + .manifest,
# NN = 01..count, so a higher NN is a newer timestamp.
seed_archives() {
  local dir="$1" count="$2" year="$3" i name
  mkdir -p "$dir"
  for (( i = 1; i <= count; i++ )); do
    name="$(printf 'kb-%s0101T0000%02dZ' "$year" "$i")"
    printf 'old archive %s\n' "$i" > "$dir/$name.tar.gz"
    printf 'archive=%s.tar.gz\n' "$name" > "$dir/$name.manifest"
  done
}

archive_name() { printf 'kb-%s0101T0000%02dZ' "$1" "$2"; }

# Count only kb-<timestamp>.tar.gz / .manifest (bystanders like kb-manual.tar.gz excluded).
count_archives() { find "$1" -maxdepth 1 -regextype posix-extended -regex '.*/kb-[0-9]{8}T[0-9]{6}Z\.tar\.gz' | wc -l | tr -d ' '; }
count_manifests() { find "$1" -maxdepth 1 -regextype posix-extended -regex '.*/kb-[0-9]{8}T[0-9]{6}Z\.manifest' | wc -l | tr -d ' '; }

# Files that are not kb-<timestamp>.tar.gz / .manifest and must never be touched.
seed_bystanders() {
  local d="$BACKUPS"
  printf 'notes\n'   > "$d/README.txt"
  printf 'manual\n'  > "$d/kb-manual.tar.gz"
  printf 'other\n'   > "$d/other-20200101T000000Z.tar.gz"
  printf 'part\n'    > "$d/kb-20200101T000000Z.tar.gz.partial"
  mkdir -p "$d/keep-me"
  printf 'nested\n'  > "$d/keep-me/kb-20190101T000000Z.tar.gz"
  # Record a checksum + mtime snapshot of every bystander.
  ( cd "$d" && find README.txt kb-manual.tar.gz other-20200101T000000Z.tar.gz \
      kb-20200101T000000Z.tar.gz.partial keep-me -type f -exec stat -c '%n %Y %s' {} + \
      | sort ) > "$BATS_TEST_TMPDIR/bystanders.before"
}

bystanders_unchanged() {
  ( cd "$BACKUPS" && find README.txt kb-manual.tar.gz other-20200101T000000Z.tar.gz \
      kb-20200101T000000Z.tar.gz.partial keep-me -type f -exec stat -c '%n %Y %s' {} + \
      | sort ) > "$BATS_TEST_TMPDIR/bystanders.after"
  diff "$BATS_TEST_TMPDIR/bystanders.before" "$BATS_TEST_TMPDIR/bystanders.after"
}

# seed_removed <source> <count>: files r01..rNN with mtimes N days ago .. 1 day ago
# (r01 oldest), so the newest by mtime are the highest numbers.
seed_removed() {
  local src="$1" count="$2" i dir="$FAKE/data/inputs/$1/.removed"
  mkdir -p "$dir"
  for (( i = 1; i <= count; i++ )); do
    printf 'removed %s\n' "$i" > "$dir/$(printf 'r%02d' "$i").md"
    touch -d "@$(( $(date +%s) - (count - i + 1) * 86400 ))" "$dir/$(printf 'r%02d' "$i").md"
  done
}

count_files() { find "$1" -maxdepth 1 -type f | wc -l | tr -d ' '; }

run_backup() { run --separate-stderr "$FAKE/scripts/backup.sh" "$@"; }

# --- SC-013: 16 backups leave the 14 newest -------------------------------

@test "after 16 successful backups exactly the 14 newest archives and manifests remain" {
  seed_archives "$BACKUPS" 15 2020
  seed_bystanders

  run_backup
  [ "$status" -eq 0 ]

  new_archive="$output"
  [ -f "$new_archive" ]
  [ -f "${new_archive%.tar.gz}.manifest" ]

  [ "$(count_archives "$BACKUPS")" -eq 14 ]
  [ "$(count_manifests "$BACKUPS")" -eq 14 ]

  # The two oldest seeded archives went, together with their manifests.
  for i in 1 2; do
    n="$(archive_name 2020 "$i")"
    [ ! -e "$BACKUPS/$n.tar.gz" ]
    [ ! -e "$BACKUPS/$n.manifest" ]
  done
  # The 13 newest seeded archives and their manifests stayed.
  for i in $(seq 3 15); do
    n="$(archive_name 2020 "$i")"
    [ -f "$BACKUPS/$n.tar.gz" ]
    [ -f "$BACKUPS/$n.manifest" ]
  done

  bystanders_unchanged
}

@test "every pruned file is logged to stderr and stdout is only the archive path" {
  seed_archives "$BACKUPS" 15 2020

  run_backup
  [ "$status" -eq 0 ]

  [ "${#lines[@]}" -eq 1 ]
  [[ "$output" == "$BACKUPS"/kb-*.tar.gz ]]

  for i in 1 2; do
    n="$(archive_name 2020 "$i")"
    grep -qE "^pruned .*/$n\.tar\.gz\$" <<<"$stderr"
    grep -qE "^pruned .*/$n\.manifest\$" <<<"$stderr"
  done
  ! grep -qF "$(archive_name 2020 3)" <<<"$(grep '^pruned ' <<<"$stderr")"
}

@test "the archive just written is never pruned, even if its timestamp is the oldest" {
  seed_archives "$BACKUPS" 14 2099

  run_backup
  [ "$status" -eq 0 ]
  [ -f "$output" ]
  [ -f "${output%.tar.gz}.manifest" ]
  ! grep -qF "$(basename "$output")" <<<"$(grep '^pruned ' <<<"$stderr")"
}

@test "fewer archives than the limit: nothing is pruned" {
  seed_archives "$BACKUPS" 5 2020

  run_backup
  [ "$status" -eq 0 ]
  [ "$(count_archives "$BACKUPS")" -eq 6 ]
  [ "$(count_manifests "$BACKUPS")" -eq 6 ]
  ! grep -q '^pruned ' <<<"$stderr"
}

@test "BACKUP_RETENTION is read from .env" {
  write_env 3
  seed_archives "$BACKUPS" 6 2020
  seed_removed drive 5

  run_backup
  [ "$status" -eq 0 ]
  [ "$(count_archives "$BACKUPS")" -eq 3 ]
  [ "$(count_manifests "$BACKUPS")" -eq 3 ]
  [ -f "$output" ]
  [ -f "$BACKUPS/$(archive_name 2020 6).tar.gz" ]
  [ -f "$BACKUPS/$(archive_name 2020 5).tar.gz" ]
  [ "$(count_files "$FAKE/data/inputs/drive/.removed")" -eq 3 ]
}

@test "BACKUP_RETENTION defaults to 14 when .env omits it" {
  write_env absent
  seed_archives "$BACKUPS" 15 2020

  run_backup
  [ "$status" -eq 0 ]
  [ "$(count_archives "$BACKUPS")" -eq 14 ]
}

# --- --output directories are never pruned --------------------------------

@test "an --output directory is never pruned, nor is data/backups/ on that run" {
  out="$BATS_TEST_TMPDIR/elsewhere"
  seed_archives "$out" 16 2020
  seed_archives "$BACKUPS" 16 2020
  seed_removed drive 16

  run_backup --output "$out"
  [ "$status" -eq 0 ]
  [[ "$output" == "$out"/kb-*.tar.gz ]]

  [ "$(count_archives "$out")" -eq 17 ]
  [ "$(count_manifests "$out")" -eq 17 ]
  [ "$(count_archives "$BACKUPS")" -eq 16 ]
  [ "$(count_manifests "$BACKUPS")" -eq 16 ]
  [ "$(count_files "$FAKE/data/inputs/drive/.removed")" -eq 16 ]
  ! grep -q '^pruned ' <<<"$stderr"
}

# --- A failed or unverified backup prunes nothing -------------------------

@test "an unverified backup (unstable checksum) exits 1 and prunes nothing" {
  seed_archives "$BACKUPS" 16 2020
  seed_removed drive 16
  seed_bystanders

  # sha256sum stub: a different digest on every call, so verification fails.
  cat > "$STUBS/sha256sum" <<EOF
#!/usr/bin/env bash
n=\$(cat "$BATS_TEST_TMPDIR/sha.count" 2>/dev/null || echo 0)
n=\$((n + 1)); echo "\$n" > "$BATS_TEST_TMPDIR/sha.count"
printf '%064d  %s\n' "\$n" "\${1:-}"
EOF
  chmod +x "$STUBS/sha256sum"

  run_backup
  [ "$status" -eq 1 ]

  [ "$(count_archives "$BACKUPS")" -eq 16 ]
  [ "$(count_manifests "$BACKUPS")" -eq 16 ]
  [ "$(count_files "$FAKE/data/inputs/drive/.removed")" -eq 16 ]
  ! grep -q '^pruned ' <<<"$stderr"
  bystanders_unchanged
}

@test "a failed backup (nothing to archive) exits 1 and prunes nothing" {
  seed_archives "$BACKUPS" 16 2020
  seed_removed drive 16
  rm -rf "$FAKE/data/rag_storage"

  run_backup
  [ "$status" -eq 1 ]

  [ "$(count_archives "$BACKUPS")" -eq 16 ]
  [ "$(count_manifests "$BACKUPS")" -eq 16 ]
  [ "$(count_files "$FAKE/data/inputs/drive/.removed")" -eq 16 ]
  ! grep -q '^pruned ' <<<"$stderr"
}

# --- .removed/ folders keep their 14 newest files -------------------------

@test "each .removed/ folder keeps its 14 newest files by mtime" {
  seed_removed drive 16
  seed_removed appflowy 3
  mkdir -p "$FAKE/data/inputs/research"
  printf 'live\n' > "$FAKE/data/inputs/drive/live.md"
  printf 'staged\n' > "$FAKE/data/inputs/drive__live.md"

  run_backup
  [ "$status" -eq 0 ]

  drive_removed="$FAKE/data/inputs/drive/.removed"
  [ "$(count_files "$drive_removed")" -eq 14 ]
  [ ! -e "$drive_removed/r01.md" ]
  [ ! -e "$drive_removed/r02.md" ]
  for i in $(seq 3 16); do
    [ -f "$drive_removed/$(printf 'r%02d' "$i").md" ]
  done
  grep -qE '^pruned .*/drive/\.removed/r01\.md$' <<<"$stderr"
  grep -qE '^pruned .*/drive/\.removed/r02\.md$' <<<"$stderr"

  [ "$(count_files "$FAKE/data/inputs/appflowy/.removed")" -eq 3 ]
  # Store files and staged copies outside .removed/ are untouched.
  [ -f "$FAKE/data/inputs/drive/live.md" ]
  [ -f "$FAKE/data/inputs/drive__live.md" ]
}

# --- BACKUP_RETENTION validation ------------------------------------------

@test "BACKUP_RETENTION=0 exits 2 and prunes nothing" {
  write_env 0
  seed_archives "$BACKUPS" 16 2020
  seed_removed drive 16

  run_backup
  [ "$status" -eq 2 ]
  grep -q 'BACKUP_RETENTION' <<<"$stderr"

  [ "$(count_archives "$BACKUPS")" -ge 16 ]
  for i in $(seq 1 16); do
    [ -f "$BACKUPS/$(archive_name 2020 "$i").tar.gz" ]
  done
  [ "$(count_files "$FAKE/data/inputs/drive/.removed")" -eq 16 ]
}

@test "a non-integer BACKUP_RETENTION exits 2" {
  write_env abc
  seed_archives "$BACKUPS" 16 2020

  run_backup
  [ "$status" -eq 2 ]
  for i in $(seq 1 16); do
    [ -f "$BACKUPS/$(archive_name 2020 "$i").tar.gz" ]
  done
}
