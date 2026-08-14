#!/usr/bin/env bash
# Start the API for a Cloud Agent boot. Uses an empty isolated data directory,
# never teacher user_data. Returns after a health check.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -s "${HOME}/.nvm/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "${HOME}/.nvm/nvm.sh"
  nvm use 22 >/dev/null 2>&1 || true
fi

PYTHON="${ROOT}/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "Missing ${ROOT}/.venv; run tools/cloud_agent_install.sh first." >&2
  exit 1
fi

API_PORT="${API_PORT:-8035}"
DATA_DIR="${AI_GRADING_WORKTREE_DATA_DIR:-/tmp/ai-grading-cloud-data}"
LOG_FILE="${AI_GRADING_CLOUD_LOG:-/tmp/ai-grading-api.log}"

export PYTHONUTF8=1
export AI_GRADING_NO_BROWSER=1
export AI_GRADING_WORKTREE_DATA_DIR="$DATA_DIR"
export AI_GRADING_DATA_DIR="$DATA_DIR"
export AI_GRADING_OPS_STATE_DIR="${AI_GRADING_OPS_STATE_DIR:-$DATA_DIR/runtime_state/ops}"
export AI_GRADING_API_PROFILES_PATH="${AI_GRADING_API_PROFILES_PATH:-$DATA_DIR/config/api_profiles.json}"

mkdir -p "$DATA_DIR" "$AI_GRADING_OPS_STATE_DIR" "$(dirname "$AI_GRADING_API_PROFILES_PATH")"

healthz() {
  "$PYTHON" - "$API_PORT" <<'PY'
import json
import sys
import urllib.error
import urllib.request

port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/healthz", timeout=1.5) as response:
        payload = json.loads(response.read().decode("utf-8"))
except (OSError, ValueError, urllib.error.URLError):
    raise SystemExit(1)
ok = (
    payload.get("status") == "ok"
    and payload.get("service") == "ai-grading-api"
)
raise SystemExit(0 if ok else 1)
PY
}

port_in_use() {
  "$PYTHON" - "$API_PORT" <<'PY'
import socket
import sys

port = int(sys.argv[1])
client = socket.socket()
client.settimeout(0.4)
try:
    result = client.connect_ex(("127.0.0.1", port))
finally:
    client.close()
raise SystemExit(0 if result == 0 else 1)
PY
}

if healthz; then
  echo "AI Grading System already healthy on http://127.0.0.1:${API_PORT}/"
  exit 0
fi

if port_in_use; then
  echo "Port ${API_PORT} is in use, but /api/healthz is not healthy." >&2
  exit 1
fi

"$PYTHON" "${ROOT}/tools/run_project_module.py" backend.startup_storage_preflight
"$PYTHON" "${ROOT}/tools/run_project_module.py" backend.api.launcher --check-frontend
"$PYTHON" "${ROOT}/tools/run_project_module.py" backend.ops.offline --apply-pending

nohup "$PYTHON" "${ROOT}/tools/run_project_module.py" backend.api.launcher \
  --host 127.0.0.1 \
  --port "$API_PORT" \
  --no-browser \
  >"$LOG_FILE" 2>&1 &

for _ in $(seq 1 40); do
  if healthz; then
    echo "AI Grading System"
    echo "Isolated data directory: ${DATA_DIR}"
    echo "URL: http://127.0.0.1:${API_PORT}/"
    exit 0
  fi
  sleep 0.5
done

echo "API did not become healthy. Last log lines:" >&2
tail -n 80 "$LOG_FILE" >&2 || true
exit 1
