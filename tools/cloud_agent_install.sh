#!/usr/bin/env bash
# Install durable Cloud Agent dependencies. This script must finish and exit.
# It does not start the API and must not touch teacher user_data.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if command -v sudo >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    python3-venv \
    python3-pip
fi

# Cursor Cloud PATH may put /exec-daemon/node (too old for this frontend) first.
# Always call the nvm Node 22 binary by absolute path.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ ! -s "${NVM_DIR}/nvm.sh" ]; then
  echo "nvm was not found; Node.js 22.18+ cannot be selected." >&2
  exit 1
fi
# shellcheck disable=SC1091
. "${NVM_DIR}/nvm.sh"
nvm install 22
NODE_VERSION="$(nvm version 22)"
NODE_HOME="${NVM_DIR}/versions/node/${NODE_VERSION}/bin"
NODE_BIN="${NODE_HOME}/node"
NPM_BIN="${NODE_HOME}/npm"
if [ ! -x "$NODE_BIN" ] || [ ! -x "$NPM_BIN" ]; then
  echo "nvm Node.js 22 was installed but ${NODE_HOME} is incomplete." >&2
  exit 1
fi

node_version="$("$NODE_BIN" -p "process.versions.node")"
node_major="${node_version%%.*}"
node_minor="${node_version#*.}"
node_minor="${node_minor%%.*}"
if [ "$node_major" -lt 22 ] || { [ "$node_major" -eq 22 ] && [ "$node_minor" -lt 18 ]; }; then
  echo "Node.js ^22.18.0 or >=24.12.0 is required; found ${node_version}." >&2
  exit 1
fi

if [ "$("$NPM_BIN" -v)" != "11.8.0" ]; then
  "$NPM_BIN" install -g npm@11.8.0
fi

echo "Using node $("$NODE_BIN" -v) and npm $("$NPM_BIN" -v) from ${NODE_HOME}"

python3 -m venv "${ROOT}/.venv"
"${ROOT}/.venv/bin/python" -m pip install -U pip
# uvicorn and python-multipart are in the portable Windows runtime, but not in
# requirements.txt. Cloud Agents do not ship that runtime, so install them here.
"${ROOT}/.venv/bin/python" -m pip install \
  -r "${ROOT}/requirements.txt" \
  -c "${ROOT}/constraints.txt" \
  "uvicorn==0.49.0" \
  "python-multipart==0.0.32"

(
  cd "${ROOT}/frontend"
  echo "Frontend build using node $("$NODE_BIN" -v) npm $("$NPM_BIN" -v)"
  "$NPM_BIN" ci
  "$NPM_BIN" run build
)
