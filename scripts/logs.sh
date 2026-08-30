#!/usr/bin/env bash
# scripts/logs.sh — tail the LightRAG container logs.
#
# Contract: contracts/operational-cli.md → logs.sh   (read-only)
#   Args:   [--tail N] [--no-follow] [SERVICE]
#           default: follow, last 200 lines, service 'lightrag'
#   3  unmet prerequisite

set -euo pipefail
# shellcheck source=scripts/lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

tail_n=200
follow=1
service="lightrag"
while (( $# )); do
  case "$1" in
    --tail) tail_n="${2:-}"; [[ "$tail_n" =~ ^[0-9]+$ ]] || die "$EXIT_USAGE" "--tail needs a number"; shift 2 ;;
    -f|--follow) follow=1; shift ;;
    --no-follow) follow=0; shift ;;
    -h|--help) printf 'Usage: logs.sh [--tail N] [--no-follow] [SERVICE]\n' >&2; exit "$EXIT_OK" ;;
    -*) die "$EXIT_USAGE" "unknown option: $1" ;;
    *) service="$1"; shift ;;
  esac
done

require_docker_daemon

args=(logs --tail "$tail_n")
(( follow )) && args+=(-f)
args+=("$service")
exec docker compose --project-directory "$REPO_ROOT" -f "$COMPOSE_FILE" "${args[@]}"
