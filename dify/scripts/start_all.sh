#!/usr/bin/env bash
# Start the whole memory backend without any model API key:
#
#   powercontext  PowerContext Server with the embedded seekdb engine
#   bridge        trusted PowerContext bridge used by the Dify HTTP nodes
#   mock-llm      OpenAI-compatible model named local-qwen2.5-0.5b
#
# Usage:
#   bash dify/scripts/start_all.sh            # docker compose if available
#   bash dify/scripts/start_all.sh docker
#   bash dify/scripts/start_all.sh native     # plain processes, no docker
#
# Containers always address each other by service name:
#   bridge        -> http://powercontext:8000
#   Dify          -> http://bridge:8787 and http://mock-llm:8099/v1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="${ROOT}/.runtime"
MODE="${1:-auto}"
DIFY_BASE_URL="${DIFY_BASE_URL:-http://127.0.0.1:8080}"
MODEL_NAME="${DIFY_MODEL:-local-qwen2.5-0.5b}"

mkdir -p "${RUNTIME}"

log() { printf '[start_all] %s\n' "$*"; }

have_docker() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

dify_network() {
  if [ -n "${DIFY_NETWORK:-}" ]; then
    printf '%s' "${DIFY_NETWORK}"
    return
  fi
  local cid="" nets="" name="" first=""
  if command -v docker >/dev/null 2>&1; then
    cid="$(docker ps --format '{{.ID}} {{.Image}}' 2>/dev/null \
      | awk '/langgenius\/dify-api|dify-api/{print $1; exit}')"
  fi
  if [ -n "${cid}" ]; then
    # An api container usually sits on several networks; pick exactly one name.
    nets="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{println}}{{end}}' "${cid}")"
    while IFS= read -r name; do
      [ -n "${name}" ] || continue
      [ -n "${first}" ] || first="${name}"
      case "${name}" in
        *default*) printf '%s' "${name}"; return ;;
      esac
    done <<<"${nets}"
    printf '%s' "${first}"
  else
    printf 'inspector-recall-net'
  fi
}

wait_url() {
  local url="$1" name="$2" tries="${3:-60}"
  for _ in $(seq 1 "${tries}"); do
    if curl -fsS -m 3 "${url}" >/dev/null 2>&1; then
      log "${name} is ready"
      return 0
    fi
    sleep 2
  done
  log "${name} did not become ready: ${url}"
  return 1
}

start_docker() {
  local net
  net="$(dify_network)"
  export DIFY_NETWORK="${net}"
  if [ -z "${POWERCONTEXT_DOCKERFILE:-}" ] \
    && compgen -G "${ROOT}/deploy/wheelhouse/*.whl" >/dev/null; then
    POWERCONTEXT_DOCKERFILE=deploy/powercontext-offline.Dockerfile
    log "using local deploy/wheelhouse (offline build)"
  fi
  export POWERCONTEXT_DOCKERFILE="${POWERCONTEXT_DOCKERFILE:-deploy/powercontext.Dockerfile}"
  docker network inspect "${net}" >/dev/null 2>&1 || docker network create "${net}" >/dev/null
  log "shared docker network: ${net}"
  log "powercontext image: ${POWERCONTEXT_DOCKERFILE}"
  docker compose -f "${ROOT}/deploy/compose.yaml" --project-name inspector-recall up -d --build
  wait_url "http://127.0.0.1:8787/health" "bridge"
  wait_url "http://127.0.0.1:8099/v1/models" "mock-llm"
  local pc
  pc="$(docker compose -f "${ROOT}/deploy/compose.yaml" --project-name inspector-recall ps -q powercontext)"
  for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "${pc}" 2>/dev/null || echo starting)"
    [ "${status}" = "healthy" ] && break
    sleep 2
  done
  log "powercontext health: ${status:-unknown} (embedded seekdb at /data/seekdb)"
}

start_native() {
  local py="${PYTHON:-python3}"
  local pc_cmd=()
  local extra="cli,server"
  [ "${POWERCONTEXT_SERVER_DATABASE_KIND:-seekdb}" = "seekdb" ] && extra="cli,server,seekdb"
  if command -v powercontext >/dev/null 2>&1; then
    pc_cmd=(powercontext)
  elif [ -x "${HOME}/.local/bin/powercontext" ]; then
    pc_cmd=("${HOME}/.local/bin/powercontext")
  elif command -v uvx >/dev/null 2>&1; then
    pc_cmd=(uvx --from "powercontext[${extra}]==1.2.0")
    if [[ "${extra}" == *seekdb* ]]; then
      # aiomysql 0.3.2 imports escape_bytes_prefixed from pymysql.converters and
      # pymysql >= 1.2 dropped it, which breaks every seekdb write.
      pc_cmd+=(--with "pymysql>=1.1,<1.2")
    fi
    pc_cmd+=(powercontext)
  else
    log "PowerContext CLI not found: install it with"
    if [[ "${extra}" == *seekdb* ]]; then
      log "  uv tool install \"powercontext[${extra}]==1.2.0\" --with 'pymysql>=1.1,<1.2'"
    else
      log "  uv tool install 'powercontext[${extra}]==1.2.0'"
    fi
    exit 1
  fi

  local db_kind="${POWERCONTEXT_SERVER_DATABASE_KIND:-seekdb}"
  local db_url="${POWERCONTEXT_SERVER_DATABASE_URL:-}"
  local db_path=""
  if [ "${db_kind}" = "oceanbase" ]; then
    # Server-mode seekdb / OceanBase: PowerContext connects over the MySQL
    # protocol instead of embedding the engine in its own process.
    if [ -z "${db_url}" ]; then
      log "database kind oceanbase needs POWERCONTEXT_SERVER_DATABASE_URL"
      log "  e.g. mysql+aoceanbase://user@127.0.0.1:10000/powercontext_dify"
      exit 1
    fi
    # PowerContext rejects an OceanBase URL without this charset.
    case "${db_url}" in
      *charset=utf8mb4*) : ;;
      *\?*) db_url="${db_url}&charset=utf8mb4" ;;
      *) db_url="${db_url}?charset=utf8mb4" ;;
    esac
  elif [ "${db_kind}" = "seekdb" ]; then
    db_path="${RUNTIME}/powercontext/seekdb"
  else
    db_path="${RUNTIME}/powercontext/powercontext.db"
  fi

  {
    echo "POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1"
    echo "POWERCONTEXT_SERVER_DATABASE_KIND=${db_kind}"
    [ -z "${db_url}" ] || echo "POWERCONTEXT_SERVER_DATABASE_URL=${db_url}"
    [ -z "${db_path}" ] || echo "POWERCONTEXT_SERVER_DATABASE_PATH=${db_path}"
    echo "POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true"
  } > "${RUNTIME}/powercontext.env"
  mkdir -p "${RUNTIME}/state"
  [ -z "${db_path}" ] || mkdir -p "$(dirname "${db_path}")"

  log "starting PowerContext (${db_kind})"
  nohup "${pc_cmd[@]}" server run --env-file "${RUNTIME}/powercontext.env" \
    > "${RUNTIME}/powercontext.log" 2>&1 &
  echo $! > "${RUNTIME}/powercontext.pid"
  wait_url "http://127.0.0.1:8000/health/ready" "powercontext" 60

  log "starting bridge"
  POWERCONTEXT_URL="http://127.0.0.1:8000" \
  BRIDGE_PORT="8787" \
  BRIDGE_DB_PATH="${RUNTIME}/state/scopes.sqlite3" \
    nohup "${py}" "${ROOT}/backend/bridge/server.py" > "${RUNTIME}/bridge.log" 2>&1 &
  echo $! > "${RUNTIME}/bridge.pid"
  wait_url "http://127.0.0.1:8787/health" "bridge" 30

  log "starting mock LLM (${MODEL_NAME})"
  MOCK_LLM_MODEL="${MODEL_NAME}" MOCK_LLM_PORT="8099" \
    nohup "${py}" "${ROOT}/scripts/mock_llm.py" > "${RUNTIME}/mock-llm.log" 2>&1 &
  echo $! > "${RUNTIME}/mock-llm.pid"
  wait_url "http://127.0.0.1:8099/v1/models" "mock-llm" 30
}

case "${MODE}" in
  docker) start_docker ;;
  native) start_native ;;
  auto)
    if have_docker; then start_docker; else start_native; fi
    ;;
  *) echo "usage: $0 [auto|docker|native]" >&2; exit 2 ;;
esac

cat <<EOF

[start_all] backend is up
  powercontext   http://powercontext:8000   (${POWERCONTEXT_SERVER_DATABASE_KIND:-seekdb})
  bridge         http://bridge:8787         (POST /api/recall, /api/remember)
  mock-llm       http://mock-llm:8099/v1    (${MODEL_NAME})

Dify settings:
  model base URL : ${MODEL_BASE_URL:-http://mock-llm:8099/v1}
  app env        : POWERCONTEXT_BRIDGE_URL=${BRIDGE_URL:-http://bridge:8787}
  SSRF allowlist : SSRF_PROXY_ALLOW_PRIVATE_IPS=<shared docker subnet>

Next:
  export DIFY_BASE_URL=${DIFY_BASE_URL} DIFY_EMAIL=... DIFY_PASSWORD=...
  python3 dify/scripts/dify_setup.py
  bash dify/scripts/demo.sh
EOF
