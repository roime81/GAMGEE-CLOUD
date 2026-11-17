# GAMGEE Control Tower — Engineering and Operations Guide

## Status

This guide describes the current engineering preview. The Control Tower now includes:
- dependency-aware installation planning and approved Ansible execution
- persistent SQLite installation job history
- a white guided UI with a Core Cloud first-run path
- Capsule workspace creation
- deterministic stateless application compilation into Deployment, Service and optional TLS-backed Ingress
- application list, scale/stop, restart and delete operations
- cluster node status
- a K3s worker join script

This is a meaningful vertical slice, not a claim of a complete cloud operating system. Review the root README capability matrix and security limitations.

## Process and trust boundary

The guided installer registers gamgee-control-tower.service as a root service. It:
- binds to 127.0.0.1:8091
- reads a generated bearer token from a root-only environment file
- records installation jobs in SQLite
- uses the host kubeconfig at /etc/rancher/k3s/k3s.yaml when present
- invokes repository-owned Ansible playbooks for installation profiles
- invokes a fixed kubectl executable through tower.cluster

Keep the process local. Use an SSH tunnel for remote access. The token is a privileged cloud-administration credential.

The Control Tower runs as root in the current preview because selected installation playbooks manage host packages and services. A future split should separate:
1. unprivileged API/intent service
2. narrow privileged host executor
3. Kubernetes service account for tenant/workload operations
4. independently authenticated users and roles

## Install and run

Guided installation on a fresh Debian/Ubuntu host:

    sudo bash scripts/install-gamgee.sh

The script installs prerequisites, prepares the virtual environment/token, writes /etc/gamgee/control-tower.env, registers the systemd unit and starts the service.

Manual mode:

    sudo bash scripts/install-control-tower.sh
    sudo bash scripts/run-control-tower.sh

Manual mode defaults to planning-only. Explicitly enable applying an approved plan with:

    sudo env GAMGEE_TOWER_ALLOW_APPLY=true bash scripts/run-control-tower.sh

The guided installer enables apply for the local, loopback-bound service so the initial owner can approve installation in the UI. This does not authorize exposing the service to an untrusted network.

## Authentication

All /v1/tower/* endpoints require Authorization: Bearer <token>. The generated 256-bit random token is stored at control-tower/.tower-token, mode 0600. The guided installer also writes a root-only copy into /etc/gamgee/control-tower.env for systemd.

There is one installation-level administrator token. There is no per-user OIDC, MFA, delegated role, tenant-scoped access token or access review workflow in this version.

## API inventory

### Health and cluster

- GET /healthz
- GET /readyz
- GET /v1/tower/capabilities
- GET /v1/tower/cloud/status

### Installation

- GET /v1/tower/profiles
- POST /v1/tower/plan
- POST /v1/tower/jobs
- GET /v1/tower/jobs
- GET /v1/tower/jobs/{job_id}

Plan body:

    {"profiles":["core-cloud","function-cloud"]}

Apply body:

    {
      "profiles":["core-cloud","function-cloud"],
      "plan_hash":"<current-plan-hash>",
      "mode":"apply",
      "approved":true
    }

### Workspace

- GET /v1/tower/workspaces
- POST /v1/tower/workspaces

Body:

    {"name":"acme","owner_email":"owner@example.com"}

Workspace creation emits a Capsule Tenant with production and staging namespaces, resource quotas and tenant labels. The request returns while Capsule completes namespace reconciliation; the production namespace may not be ready immediately.

### Application

- GET /v1/tower/workspaces/{workspace}/apps
- POST /v1/tower/workspaces/{workspace}/apps
- POST /v1/tower/workspaces/{workspace}/apps/{app}/scale
- POST /v1/tower/workspaces/{workspace}/apps/{app}/resume
- POST /v1/tower/workspaces/{workspace}/apps/{app}/restart

Scaling to zero stores the previous positive replica count in a GAMGEE-managed Deployment annotation. The resume endpoint restores that value (bounded to 1–20, defaulting to one if missing or invalid).
- DELETE /v1/tower/workspaces/{workspace}/apps/{app}

Example body:

    {
      "name":"portal",
      "image":"ghcr.io/example/portal:v1.2.3",
      "port":8080,
      "replicas":1,
      "size":"small",
      "health_path":"/"
    }

Size tiers:
- small: requests 100m CPU / 128Mi memory; limits 500m / 512Mi
- standard: requests 250m / 256Mi; limits 1 CPU / 1Gi
- large: requests 500m / 512Mi; limits 2 CPU / 2Gi

The request model rejects :latest, untagged image names, arbitrary extra fields and ports below 1024. Workspace/app names are DNS labels. Public hostname and TLS secret must be supplied together.

## Application compiler and security profile

The compiler creates:
- apps/v1 Deployment
- v1 ClusterIP Service
- optional networking.k8s.io/v1 Ingress with Traefik class, websecure entrypoint and pre-existing TLS Secret

Pod security:
- ServiceAccount token automount disabled
- RuntimeDefault seccomp
- runAsNonRoot: true
- allowPrivilegeEscalation: false
- readOnlyRootFilesystem: true
- all Linux capabilities dropped
- only a bounded 64Mi /tmp emptyDir is writable by default
- CPU/memory requests and limits selected from predefined tiers
- HTTP readiness and liveness probes

Application images must satisfy these constraints. An image expecting root, a writable root filesystem or extra capabilities will not run without a separately reviewed security design. There are no arbitrary command, hostPath, volume, secret or privileged-container fields in the API.

The app API uses only the <workspace>-production namespace. It verifies the namespace has the gamgee.cloud/tenant label before applying or operating resources. Listing only returns Deployments labeled gamgee.cloud/managed-by=gamgee.

Delete removes Deployment, Service and Ingress resources by validated app name. It does not remove volumes or data.

## Kubernetes command adapter

tower.cluster.run_kubectl:
- resolves the executable using shutil.which
- builds argv as a Python list
- never invokes a shell
- passes manifest YAML through stdin only for deterministic compiler output
- applies a timeout
- maps missing resources into ClusterNotFound
- bounds error text returned by the API

Callers must never pass user-supplied command arguments or manifest bodies directly to this adapter. Keep resource kinds, flags and operation choices fixed in code. Add a test for every new command path.

## Installation execution

The profile planner:
- validates selected profile IDs
- resolves dependencies
- totals profile resource estimates
- checks host CPU, available RAM, disk and required commands
- checks source playbook/artifact paths are inside the repository
- fingerprints profile data and source-file SHA-256 checksums

The runner:
- serializes host installation jobs
- allows only repository playbooks under automation/ansible
- enforces a maximum timeout
- stores output in bounded SQLite logs
- records failure without automatically destroying shared infrastructure

Automatic destructive rollback is intentionally absent. Inspect the state of each component before retrying or repairing a failed installation.

## Worker-node joining

Run scripts/join-compute-node.sh as root on a new worker:

    sudo bash scripts/join-compute-node.sh https://<control-plane-private-dns-or-ip>:6443

It prompts for the node token without echo, stores no token file and downloads the official K3s installer over TLS. Set GAMGEE_K3S_VERSION to the exact server version to avoid version drift. Restrict cluster-control and CNI traffic to trusted private networks.

This creates a K3s agent node only. It does not create an HA control plane, an Incus cluster, shared storage or node pools.

## Tests

    PYTHONPATH=control-tower python -m unittest discover -s control-tower/tests -v

Tests cover profile loading/dependency resolution, planning, job persistence, playbook path safety, workload schema/compiler and Kubernetes adapter argv behavior.

CI also runs AI service unit tests, shell syntax checks and Ansible syntax checks. CI does not provision a real K3s cluster. A disposable-VM acceptance test is required before a production rollout.

## Recovery and data handling

- Do not treat a failed install job as permission to run teardown.
- Do not delete K3s state as an automatic rollback.
- Do not run docker compose down -v unless the intention is to destroy AI Factory data volumes.
- Do not delete a workspace namespace as an app rollback.
- The Control Tower SQLite DB stores job history only; it is not a cluster backup.
- The app API currently creates stateless Deployment/Service/Ingress resources only.
- Back up K3s and application persistent data separately before production use.

## Known production blockers

1. Per-user OIDC/MFA and tenant-scoped authorization.
2. Enforced NetworkPolicy support: the default Flannel CNI does not enforce Kubernetes NetworkPolicy objects.
3. Least-privilege separation of API and privileged host executor.
4. Secret management and certificate automation.
5. Registry signature verification and workload admission policy.
6. Automated backups and tested restore.
7. Three-server HA control plane and API failover.
8. End-to-end tests for real Capsule, K3s, app scheduling, ingress, TLS and node join.
9. Immutable, identity-attributed audit trail.
