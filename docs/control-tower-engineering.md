# GAMGEE Control Tower Engineering Guide

## Status

This commit adds the first executable Control Tower foundation and seven declarative installation profiles. It is an early engineering baseline, not a claim that every enterprise capability is complete.

## Security model

- The API binds to 127.0.0.1 by default.
- All management endpoints require a 256-bit bearer token.
- The token is generated locally and stored in control-tower/.tower-token with mode 0600.
- Mutations are disabled unless GAMGEE_TOWER_ALLOW_APPLY=true.
- Apply requests require a fresh plan hash and approved=true.
- The runner executes only Ansible playbooks located under automation/ansible.
- Shell strings and commands are not accepted from API callers.
- Do not expose the Tower port directly to the Internet. Put it behind an authenticated TLS reverse proxy or private access network.
- The Control Tower host process is privileged because its playbooks manage host services. Run it only on a dedicated, trusted management node.

## Install and run

On a supported Ubuntu/Debian host:

    sudo bash scripts/install-control-tower.sh
    sudo bash scripts/run-control-tower.sh

The launcher starts in planning-only mode. To explicitly permit host changes, start it with:

    sudo env GAMGEE_TOWER_ALLOW_APPLY=true bash scripts/run-control-tower.sh

Open http://127.0.0.1:8091 and read the API token from control-tower/.tower-token (use sudo cat when required). The UI displays whether apply is enabled.

The API is also documented at /docs. Health is available at /healthz.

## Planning API

- GET /v1/tower/profiles
- POST /v1/tower/plan with {profiles:[...]}
- POST /v1/tower/jobs with profiles, plan_hash, mode=dry-run|apply and approved=true for apply
- GET /v1/tower/jobs
- GET /v1/tower/jobs/{id}

A plan includes dependency resolution, resource estimates, host preflight, required tool checks and a SHA-256 fingerprint. Generate a new plan after changing the selected profiles or bundle definitions.

## Profile implementation boundaries

- Core Cloud runs the existing K3s/Capsule/Traefik/OpenCost bootstrap and verifies the Kubernetes API.
- Function Cloud installs KEDA. The full GAMGEE Function API, WASM runtime controller and native function lifecycle are not delivered by this initial Control Tower slice.
- Private IaaS installs Incus and QEMU and initialises the default Incus profile. VM guest capacity is additional to the platform estimate.
- AI Enterprise starts the existing Docker Compose AI Factory after validating the local .env file. GPU configuration and model downloads remain explicit operator choices.
- Enterprise Security installs Kyverno. OpenBao, certificate lifecycle, image signing enforcement and complete compliance policies remain subsequent integrations.
- Federated Observability installs the VictoriaMetrics Kubernetes stack. Remote-site registration, tenant federation, trace/log backends and central multi-site routing remain subsequent integrations.
- Hybrid Cloud validates a provider-neutral OpenTofu workspace. It deliberately does not create remote resources or handle cloud credentials.

## Resource sizing

Bundle resource requirements are additive planning estimates, not guarantees. VM guests, model weights, data retention, backups and customer workloads require additional capacity. AI Enterprise CPU inference can need substantially more RAM; GPU VRAM is workload/model dependent.

## Rollback and recovery

The first release does not automatically uninstall prerequisites or destroy state after a failed bundle. This is intentional: deleting a shared dependency or data volume can cause a wider outage.

- Helm-managed components should be rolled back with the recorded Helm release revision.
- Docker Compose stacks should be stopped without the -v flag unless data deletion is explicitly approved.
- Incus guests and storage pools are retained; do not run incus admin shutdown or delete storage pools as automatic rollback.
- K3s teardown is never an automatic rollback action.
- OpenTofu remote changes require a reviewed plan and provider-specific recovery; this initial hybrid profile performs validation only.
- Inspect the job log and actual component state before retrying.

## Tests

    cd control-tower
    python -m pip install -r requirements.txt
    PYTHONPATH=. python -m unittest discover -s tests -v

The GitHub Actions workflow runs the Control Tower unit tests and the existing AI service test suites.
