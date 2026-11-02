#!/usr/bin/env bash
# Applies the default-deny network policies to every namespace of a Capsule tenant.
# Usage: scripts/apply-network-policies.sh <tenant-name>
set -Eeuo pipefail

tenant="${1:-}"
if [[ ! "$tenant" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
  echo "Usage: $0 <tenant-name> (lowercase DNS label)" >&2
  exit 2
fi
command -v envsubst >/dev/null || { echo "envsubst (gettext) is required." >&2; exit 1; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for ns in "${tenant}-production" "${tenant}-staging"; do
  TENANT_NAMESPACE="$ns" envsubst '${TENANT_NAMESPACE}' \
    < "$root/platform/capsule/network-policies.yaml" | kubectl apply -f -
done
