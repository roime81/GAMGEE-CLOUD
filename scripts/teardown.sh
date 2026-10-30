#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "--yes" ]]; then
  confirm="DELETE GAMGEE CLOUD"
else
  echo "This removes the K3s cluster and all workloads on this host."
  read -r -p 'Type "DELETE GAMGEE CLOUD" to continue: ' confirm
fi

if [[ "$confirm" != "DELETE GAMGEE CLOUD" ]]; then
  echo "Teardown cancelled."
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
else
  command -v sudo >/dev/null || {
    echo "sudo is required to remove K3s." >&2
    exit 1
  }
  SUDO=(sudo)
fi

if [[ -x /usr/local/bin/k3s-uninstall.sh ]]; then
  "${SUDO[@]}" /usr/local/bin/k3s-uninstall.sh
fi

echo "K3s and its workloads have been removed."
