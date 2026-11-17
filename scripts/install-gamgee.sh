#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# If AI secrets have already been configured, restrict them before the local
# capability detector considers enabling the AI Factory profile.
if [[ -f "$ROOT/.env" ]]; then
  chmod 0600 "$ROOT/.env"
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run once with sudo: sudo bash scripts/install-gamgee.sh" >&2
  exit 1
fi

if ! grep -qiE '^(ID|ID_LIKE)=(debian|ubuntu)' /etc/os-release; then
  echo "The guided installer currently supports Debian and Ubuntu hosts." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssl python3 python3-venv python3-pip

bash "$ROOT/scripts/install-control-tower.sh"

TOKEN_FILE="$ROOT/control-tower/.tower-token"
TOKEN="$(cat "$TOKEN_FILE")"
install -d -m 0700 /etc/gamgee
umask 077
cat > /etc/gamgee/control-tower.env <<EOF
GAMGEE_TOWER_API_TOKEN=$TOKEN
GAMGEE_TOWER_ALLOW_APPLY=true
GAMGEE_TOWER_HOST=127.0.0.1
GAMGEE_TOWER_PORT=8091
GAMGEE_TOWER_DB_PATH=$ROOT/control-tower/var/jobs.sqlite3
KUBECONFIG=/etc/rancher/k3s/k3s.yaml
EOF
chmod 0600 /etc/gamgee/control-tower.env
unset TOKEN

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd was not found. Control Tower dependencies are installed."
  echo "Start manually with: sudo bash scripts/run-control-tower.sh"
  exit 0
fi

cat > /etc/systemd/system/gamgee-control-tower.service <<EOF
[Unit]
Description=GAMGEE Cloud Control Tower
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$ROOT
EnvironmentFile=/etc/gamgee/control-tower.env
ExecStart=$ROOT/control-tower/.venv/bin/uvicorn tower.main:app --app-dir $ROOT/control-tower --host 127.0.0.1 --port 8091
Restart=on-failure
RestartSec=5
UMask=0077
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now gamgee-control-tower.service

for attempt in $(seq 1 20); do
  if curl --fail --silent http://127.0.0.1:8091/healthz >/dev/null; then
    break
  fi
  sleep 1
done

echo
echo "GAMGEE Control Tower is installed and listening on http://127.0.0.1:8091"
echo "Open the UI from this machine, or use an SSH tunnel for remote access:"
echo "  ssh -L 8091:127.0.0.1:8091 <user>@<gamgee-host>"
echo
echo "Control Tower token (local root access only):"
echo "  sudo cat $TOKEN_FILE"
echo
echo "Next: connect with the token and click 'Build my cloud' to install the host-compatible GAMGEE Local Cloud bundle. The Private IaaS foundation is included when /dev/kvm is available; the private AI stack is included when Docker Compose and configured secrets are ready."
echo "The API remains loopback-bound. Do not expose port 8091 directly to the Internet."
