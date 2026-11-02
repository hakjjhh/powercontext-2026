#!/usr/bin/env bash
# Stop whatever start_all.sh started (docker compose project or native pids).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/.runtime"

log() { printf '[stop_all] %s\n' "$*"; }

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker compose -f "${ROOT}/deploy/compose.yaml" --project-name inspector-recall down --remove-orphans || true
  log "docker compose project inspector-recall stopped"
fi

for name in powercontext bridge mock-llm; do
  pidfile="${RUNTIME}/${name}.pid"
  if [ -f "${pidfile}" ]; then
    pid="$(cat "${pidfile}")"
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      log "stopped ${name} (pid ${pid})"
    fi
    rm -f "${pidfile}"
  fi
done
