#!/usr/bin/env bash
# Run the three-turn demo and collect every artifact under verification/<out>/.
#   DEMO_OUT_DIR=verification/e2e-seekdb  -> write evidence somewhere else
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${DEMO_OUT_DIR:-verification/e2e-mock}"
case "${OUT}" in
  /*) : ;;
  *) OUT="${ROOT}/${OUT}" ;;
esac
mkdir -p "${OUT}"

# Only logs from this run, so the bridge log cannot mix in an earlier attempt.
# Epoch seconds: docker parses a bare integer as a Unix timestamp, which avoids
# the daemon's local-timezone interpretation of RFC3339 strings without offset.
RUN_SINCE="$(date +%s)"
python3 "${ROOT}/scripts/demo.py"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  bridge_cid="$(docker compose -f "${ROOT}/deploy/compose.yaml" --project-name inspector-recall ps -q bridge 2>/dev/null || true)"
  mock_cid="$(docker compose -f "${ROOT}/deploy/compose.yaml" --project-name inspector-recall ps -q mock-llm 2>/dev/null || true)"
  if [ -n "${bridge_cid}" ]; then
    # docker logs honours --since; "compose logs --since" silently ignores it.
    docker logs --since "${RUN_SINCE}" "${bridge_cid}" 2>&1 \
      | grep -avE '"GET /health' > "${OUT}/bridge-log.txt" || true
    [ -n "${mock_cid}" ] && docker logs --since "${RUN_SINCE}" "${mock_cid}" > "${OUT}/mock-llm.log" 2>&1 || true
    echo "[demo] captured bridge/mock logs from docker"
  fi
fi

if [ ! -s "${OUT}/bridge-log.txt" ] && [ -f "${ROOT}/.runtime/bridge.log" ]; then
  cp "${ROOT}/.runtime/bridge.log" "${OUT}/bridge-log.txt"
  cp "${ROOT}/.runtime/mock-llm.log" "${OUT}/mock-llm.log" 2>/dev/null || true
  echo "[demo] captured bridge/mock logs from .runtime/"
fi

# Which storage engine actually served this run? Credentials are masked.
# Prefer the container that answered the calls; fall back to the native env file.
capture_backend_docker() {
  local cid
  cid="$(docker ps --format '{{.ID}} {{.Image}}' 2>/dev/null | awk '/powercontext/{print $1; exit}')"
  [ -n "${cid}" ] || return 1
  {
    echo "# serving PowerContext container: $(docker inspect -f '{{.Config.Image}}' "${cid}")"
    echo '$ docker exec <powercontext> env | grep POWERCONTEXT_SERVER_DATABASE'
    docker exec "${cid}" sh -c 'env | grep POWERCONTEXT_SERVER_DATABASE'
    echo '--- engine data directory ---'
    docker exec "${cid}" sh -c 'ls -l /data/seekdb 2>/dev/null' || true
    echo '--- startup log (matching lines) ---'
    docker logs "${cid}" 2>&1 | grep -aiE 'database|seekdb|oceanbase|sqlite|ready|Uvicorn running' | tail -20
  } | sed -E 's#://([^:/]+):[^@/]+@#://\1:***@#g' > "${OUT}/powercontext-backend.txt"
  return 0
}

capture_backend_native() {
  [ -f "${ROOT}/.runtime/powercontext.env" ] || return 1
  sed -E 's#://([^:/]+):[^@/]+@#://\1:***@#g' "${ROOT}/.runtime/powercontext.env" \
    > "${OUT}/powercontext-backend.txt"
  echo "--- startup log (matching lines) ---" >> "${OUT}/powercontext-backend.txt"
  grep -aiE 'database|seekdb|oceanbase|sqlite|listening|Uvicorn running' \
    "${ROOT}/.runtime/powercontext.log" 2>/dev/null | tail -20 >> "${OUT}/powercontext-backend.txt" || true
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  capture_backend_docker || capture_backend_native
else
  capture_backend_native
fi

# Engine-level proof: the seekdb data directory and a recall read straight from
# the bridge after the run, so the memory shown in the transcript is traceable
# to the storage engine rather than to Dify's conversation context.
capture_engine_state() {
  local cid="${1:-}"
  {
    echo "# embedded seekdb storage proof"
    date -Iseconds
    if [ -n "${cid}" ]; then
      echo "\$ docker exec <powercontext> env | grep POWERCONTEXT_SERVER_DATABASE"
      docker exec "${cid}" sh -c 'env | grep POWERCONTEXT_SERVER_DATABASE'
      echo
      echo '$ ls -lR /data/seekdb (top level)'
      docker exec "${cid}" sh -c 'ls -l /data/seekdb' || true
      echo
      echo '$ du -sh /data/seekdb/*'
      docker exec "${cid}" sh -c 'du -sh /data/seekdb/*' || true
      echo
      echo '# seekdb engine log head'
      docker exec "${cid}" sh -c 'head -c 400 /data/seekdb/log/seekdb.log 2>/dev/null' || true
      echo
    else
      echo "# native run: engine directory ${RUNTIME_SEEKDB:-${ROOT}/.runtime/powercontext/seekdb}"
      ls -l "${ROOT}/.runtime/powercontext/seekdb" 2>/dev/null || true
      du -sh "${ROOT}/.runtime/powercontext/seekdb"/* 2>/dev/null || true
    fi
  } > "${OUT}/seekdb-storage.txt"

  python3 - "${OUT}/seekdb-recall.json" <<'PY'
import json, os, sys, urllib.request
bridge = os.getenv("DEMO_BRIDGE_URL", "http://127.0.0.1:8787")
user = os.getenv("DEMO_USER", "e2e-mock-player")
request = urllib.request.Request(
    bridge.rstrip("/") + "/api/recall",
    data=json.dumps({"session_id": user, "query": "What did I promise?"}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.loads(response.read().decode("utf-8", errors="replace"))
payload["_captured_at"] = __import__("datetime").datetime.now().astimezone().isoformat()
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

pc_cid="$(docker ps --format '{{.ID}} {{.Image}}' 2>/dev/null | awk '/powercontext/{print $1; exit}')"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && [ -n "${pc_cid}" ]; then
  capture_engine_state "${pc_cid}"
elif [ -d "${ROOT}/.runtime/powercontext/seekdb" ]; then
  capture_engine_state ""
fi

echo "[demo] artifacts:"
ls -1 "${OUT}"
