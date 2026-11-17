#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="python3"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/install-control-tower.sh" >&2
  exit 1
fi

command -v "$PYTHON_BIN" >/dev/null || {
  echo "Python 3 is required." >&2
  exit 1
}
command -v openssl >/dev/null || {
  echo "OpenSSL is required to generate the local API token." >&2
  exit 1
}

"$PYTHON_BIN" -m venv "$ROOT/control-tower/.venv"
"$ROOT/control-tower/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/control-tower/.venv/bin/pip" install -r "$ROOT/control-tower/requirements.txt"

mkdir -p "$ROOT/control-tower/var"
chmod 0750 "$ROOT/control-tower/var"

TOKEN_FILE="$ROOT/control-tower/.tower-token"
if [[ ! -s "$TOKEN_FILE" ]]; then
  umask 077
  openssl rand -hex 32 > "$TOKEN_FILE"
fi
chmod 0600 "$TOKEN_FILE"

echo "Control Tower dependencies installed."
echo "API token stored in $TOKEN_FILE (root-readable only)."
echo "Start with: sudo bash scripts/run-control-tower.sh"
