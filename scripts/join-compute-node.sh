#!/usr/bin/env bash
set -Eeuo pipefail

# Join a Linux worker to an existing GAMGEE K3s cluster.
# The node token is read without echo and is never written to a file.

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo bash scripts/join-compute-node.sh https://CONTROL-PLANE:6443" >&2
  exit 1
fi

SERVER_URL="${1:-}"
NODE_NAME="${2:-}"

if [[ -z "$SERVER_URL" ]]; then
  read -r -p "GAMGEE control-plane API URL (https://host:6443): " SERVER_URL
fi
if [[ ! "$SERVER_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]]; then
  echo "Use an HTTPS control-plane URL such as https://cloud.internal:6443." >&2
  exit 1
fi

if systemctl is-active --quiet k3s-agent; then
  echo "This machine already has an active k3s-agent service. No changes made."
  exit 0
fi

if [[ -z "$NODE_NAME" ]]; then
  NODE_NAME="$(hostname -s)"
fi
if [[ ! "$NODE_NAME" =~ ^[a-z0-9]([a-z0-9.-]{0,61}[a-z0-9])?$ ]]; then
  echo "Node name must be a lowercase DNS-compatible name." >&2
  exit 1
fi

if [[ -z "${GAMGEE_NODE_TOKEN:-}" ]]; then
  read -r -s -p "Paste the node token from the control-plane host: " GAMGEE_NODE_TOKEN
  echo
fi
if [[ -z "$GAMGEE_NODE_TOKEN" ]]; then
  echo "A K3s node token is required." >&2
  exit 1
fi

if [[ -n "${GAMGEE_K3S_VERSION:-}" ]]; then
  if [[ ! "$GAMGEE_K3S_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+$ ]]; then
    echo "GAMGEE_K3S_VERSION must match the control-plane version, e.g. v1.31.2+k3s1." >&2
    exit 1
  fi
  export INSTALL_K3S_VERSION="$GAMGEE_K3S_VERSION"
fi

command -v curl >/dev/null || {
  echo "curl is required. Install it before joining this node." >&2
  exit 1
}

installer="$(mktemp)"
cleanup() {
  rm -f "$installer"
  unset GAMGEE_NODE_TOKEN K3S_TOKEN
}
trap cleanup EXIT

echo "Downloading the official K3s installer..."
curl --proto '=https' --tlsv1.2 -fsSL https://get.k3s.io -o "$installer"
chmod 0700 "$installer"

export K3S_URL="$SERVER_URL"
export K3S_TOKEN="$GAMGEE_NODE_TOKEN"
export K3S_NODE_NAME="$NODE_NAME"

echo "Joining $NODE_NAME to $SERVER_URL ..."
INSTALL_K3S_EXEC="agent" sh "$installer"

echo "Worker join requested. Check node readiness from the GAMGEE control plane with: kubectl get nodes"
