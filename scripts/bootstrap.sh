#!/usr/bin/env bash
set -Eeuo pipefail

echo "=== Bootstrapping Gamgee Cloud on a single VM ==="

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
else
  command -v sudo >/dev/null || {
    echo "sudo is required to install system components." >&2
    exit 1
  }
  SUDO=(sudo)
fi

if ! grep -qiE '^(ID|ID_LIKE)=(debian|ubuntu)' /etc/os-release; then
  echo "This bootstrap supports Debian and Ubuntu hosts only." >&2
  exit 1
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y ca-certificates curl git gnupg

if ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | "${SUDO[@]}" sh -s - \
    --write-kubeconfig-mode 0600 \
    --disable traefik
fi

if ! command -v helm >/dev/null 2>&1; then
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
    -o "$installer"
  "${SUDO[@]}" bash "$installer"
  rm -f "$installer"
  trap - EXIT
fi

kubeconfig="/etc/rancher/k3s/k3s.yaml"
if [[ "$(id -u)" -eq 0 ]]; then
  export KUBECONFIG="$kubeconfig"
else
  mkdir -p "$HOME/.kube"
  "${SUDO[@]}" install -m 0600 -o "$(id -u)" -g "$(id -g)" \
    "$kubeconfig" "$HOME/.kube/config"
  export KUBECONFIG="$HOME/.kube/config"
fi

helm repo add projectcapsule https://projectcapsule.github.io/charts
helm repo add traefik https://traefik.github.io/charts
helm repo add opencost https://opencost.github.io/opencost-helm-chart
helm repo update

helm upgrade --install capsule projectcapsule/capsule \
  --namespace capsule-system \
  --create-namespace \
  --set manager.options.enable-cnpg=false

helm upgrade --install traefik traefik/traefik \
  --namespace kube-system \
  --set ingressClass.enabled=true \
  --set ingressClass.isDefaultClass=true

helm upgrade --install opencost opencost/opencost \
  --namespace opencost \
  --create-namespace

echo "=== Bootstrap complete. Kubeconfig: ${KUBECONFIG} ==="
