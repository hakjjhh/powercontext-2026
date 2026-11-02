#!/usr/bin/env bash
# Download every wheel for the embedded-seekdb PowerContext image exactly once.
#
# Both consumers read the same directory:
#   * container build -> deploy/powercontext-offline.Dockerfile
#   * native install  -> pip/uv install --no-index --find-links deploy/wheelhouse
#
# Usage:
#   bash dify/scripts/fetch_wheelhouse.sh
#   PYTHON=python3.12 bash dify/scripts/fetch_wheelhouse.sh   # match the image's interpreter
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash dify/scripts/fetch_wheelhouse.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${DEST:-${ROOT}/deploy/wheelhouse}"
PY="${PYTHON:-python3}"
SPEC="${POWERCONTEXT_SPEC:-powercontext[cli,server,seekdb]==1.2.0}"
INDEX="${PIP_INDEX_URL:-https://pypi.org/simple}"

mkdir -p "${DEST}"
echo "[wheelhouse] index: ${INDEX}"
"${PY}" -m pip download --index-url "${INDEX}" --timeout "${PIP_TIMEOUT:-120}" \
  --retries "${PIP_RETRIES:-8}" -d "${DEST}" "${SPEC}"

echo "[wheelhouse] ${DEST}:"
ls -1 "${DEST}" | wc -l
echo "next:"
echo "  POWERCONTEXT_DOCKERFILE=deploy/powercontext-offline.Dockerfile bash ${ROOT}/scripts/start_all.sh docker"
echo "  pip install --no-index --find-links ${DEST} '${SPEC}'"
