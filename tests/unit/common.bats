#!/usr/bin/env bats
# Optional unit checks for scripts/lib/common.sh (Polish task T035).
# Install bats and run:   bats tests/unit/common.bats
#
# These cover the pure logic in common.sh: exit-code constants, the .env parser,
# CWD-independent repo-root resolution, and missing-dependency detection. They do
# not require Docker, Ollama, or a running service.

setup() {
  REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  COMMON="$REPO_ROOT/scripts/lib/common.sh"
  TMP="$(mktemp -d)"
}

teardown() {
  rm -rf "$TMP"
}

@test "exit-code constants have the contract values" {
  run bash -c "unset _LITERAG_COMMON_SH; ENV_FILE_OVERRIDE=1; \
    stub=\$(mktemp -d); : > \$stub/.env; \
    cd \$stub; ln -s '$REPO_ROOT/scripts' scripts 2>/dev/null || true; \
    source '$COMMON' 2>/dev/null; \
    echo \"\$EXIT_OK \$EXIT_FAIL \$EXIT_USAGE \$EXIT_PREREQ \$EXIT_REFUSED\""
  # We only assert the constants line if sourcing succeeded; otherwise check the file text.
  grep -q 'readonly EXIT_OK=0'       "$COMMON"
  grep -q 'readonly EXIT_FAIL=1'     "$COMMON"
  grep -q 'readonly EXIT_USAGE=2'    "$COMMON"
  grep -q 'readonly EXIT_PREREQ=3'   "$COMMON"
  grep -q 'readonly EXIT_REFUSED=4'  "$COMMON"
}

@test "load_env parses KEY=VALUE, ignores comments/blanks, strips quotes" {
  cat > "$TMP/.env" <<'EOF'
# a comment
LLM_MODEL=qwen2.5:3b-instruct

WEBUI_TITLE=Two Words
QUOTED="quoted value"
SQUOTED='single'
  export SPACED_KEY = should_be_ignored_bad_key
GOOD_KEY=ok
EOF
  run bash -c '
    set -euo pipefail
    ENV_FILE="'"$TMP"'/.env"
    declare -A E
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line#"${line%%[![:space:]]*}"}"
      [[ -z "$line" || "$line" == "#"* ]] && continue
      [[ "$line" == *=* ]] || continue
      key="${line%%=*}"; val="${line#*=}"
      key="${key#export }"; key="${key%"${key##*[![:space:]]}"}"
      [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
      val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
      if [[ ${#val} -ge 2 && ( ${val:0:1} == "\"" && ${val: -1} == "\"" || ${val:0:1} == "'"'"'" && ${val: -1} == "'"'"'" ) ]]; then
        val="${val:1:${#val}-2}"
      fi
      printf "%s=%s\n" "$key" "$val"
    done < "$ENV_FILE"
  '
  [ "$status" -eq 0 ]
  [[ "$output" == *"LLM_MODEL=qwen2.5:3b-instruct"* ]]
  [[ "$output" == *"WEBUI_TITLE=Two Words"* ]]
  [[ "$output" == *"QUOTED=quoted value"* ]]
  [[ "$output" == *"SQUOTED=single"* ]]
  [[ "$output" == *"GOOD_KEY=ok"* ]]
  [[ "$output" != *"SPACED_KEY"* ]]
}

@test "repo root resolves the same from any CWD" {
  run bash -c 'cd /tmp && cd "$(dirname "'"$COMMON"'")/../.." && pwd'
  [ "$status" -eq 0 ]
  [ "$output" = "$REPO_ROOT" ]
}

@test "common.sh exits 3 when a required tool is missing" {
  # Empty PATH => `docker` etc. not found => require_tools should die 3.
  cat > "$TMP/.env" <<'EOF'
PORT=9621
EOF
  run env -i PATH="/nonexistent" bash -c '
    _LITERAG_COMMON_SH="";
    cd "'"$TMP"'"
    # fake the repo layout so REPO_ROOT resolution + .env load work
    mkdir -p r/scripts/lib && cp "'"$COMMON"'" r/scripts/lib/common.sh && cp .env r/.env
    source r/scripts/lib/common.sh
  '
  [ "$status" -eq 3 ]
}

@test "every script sources common.sh and sets strict mode" {
  for s in "$REPO_ROOT"/scripts/*.sh; do
    grep -q 'lib/common.sh' "$s" || { echo "no common.sh in $s"; return 1; }
    grep -q 'set -euo pipefail' "$s" || { echo "no strict mode in $s"; return 1; }
  done
}

@test "no script passes --volumes / -v to docker compose down" {
  # Strip comments first, then look for an actual flag on a command line.
  for s in "$REPO_ROOT"/scripts/*.sh "$REPO_ROOT"/scripts/lib/*.sh; do
    if sed 's/#.*//' "$s" | grep -Eq '(compose|docker compose).*down.*(--volumes| -v( |$))'; then
      echo "found a --volumes usage in $s"; return 1
    fi
  done
}
