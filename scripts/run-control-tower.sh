#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/run-control-tower.sh" >&2
  exit 1
fi

TOKEN_FILE="$ROOT/control-tower/.tower-token"
if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "Control Tower token is missing. Run scripts/install-control-tower.sh first." >&2
  exit 1
fi
if [[ ! -x "$ROOT/control-tower/.venv/bin/uvicorn" ]]; then
  echo "Dependencies are missing. Run scripts/install-control-tower.sh first." >&2
  exit 1
fi

export PATH="$ROOT/control-tower/.venv/bin:$PATH"
export GAMGEE_TOWER_API_TOKEN
GAMGEE_TOWER_API_TOKEN="$(cat "$TOKEN_FILE")"
if ! printenv GAMGEE_TOWER_ALLOW_APPLY >/dev/null 2>&1; then
  export GAMGEE_TOWER_ALLOW_APPLY="false"
fi
if ! printenv GAMGEE_TOWER_HOST >/dev/null 2>&1; then
  export GAMGEE_TOWER_HOST="127.0.0.1"
fi
if ! printenv GAMGEE_TOWER_PORT >/dev/null 2>&1; then
  export GAMGEE_TOWER_PORT="8091"
fi
if ! printenv GAMGEE_TOWER_DB_PATH >/dev/null 2>&1; then
  export GAMGEE_TOWER_DB_PATH="$ROOT/control-tower/var/jobs.sqlite3"
fi

cd "$ROOT"
echo "GAMGEE Control Tower: http://$GAMGEE_TOWER_HOST:$GAMGEE_TOWER_PORT"
echo "Token file: $TOKEN_FILE"
exec "$ROOT/control-tower/.venv/bin/uvicorn" tower.main:app \
  --app-dir "$ROOT/control-tower" \
  --host "$GAMGEE_TOWER_HOST" \
  --port "$GAMGEE_TOWER_PORT"
