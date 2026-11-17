# 08 — Security and Operations

## Security posture

GAMGEE uses typed resource contracts, constrained drivers and ownership checks to reduce arbitrary infrastructure mutation. The current engineering preview has important limitations and must not be represented as security-certified or production-hardened.

## Current trust boundaries

| Boundary | Current behaviour | Important limitation |
|---|---|---|
| Control Tower access | One installation-level bearer token | No per-user OIDC/MFA/RBAC in this version |
| Network exposure | Control Tower binds to loopback by default | Remote access should use a protected SSH tunnel |
| Host execution | Control Tower systemd service runs as root | Privileged process; future split executor needed |
| Resource API | Typed schemas and fixed drivers | Policy engine and approval framework incomplete |
| Tenant isolation | Capsule namespaces and quotas | Default Flannel CNI does not enforce Kubernetes NetworkPolicy |
| Workload security | Non-root, dropped capabilities, seccomp, read-only root filesystem defaults for Applications | Does not replace node hardening or hostile-tenant isolation |
| Function runtime | Spin/WASM, tokenless service account, bounded resources | Real-cluster security acceptance and network enforcement remain gaps |
| Secrets | Existing Kubernetes TLS Secret references | OpenBao integration and rotation are planned |
| State | Local SQLite | No shared HA database or distributed worker coordination |
| AI tools | Configured MCP endpoint list | Complete tool governance and end-to-end audit are not yet delivered |

## Secure operating rules

- Keep Control Tower bound to loopback.
- Use SSH tunnelling or a separately secured access gateway.
- Protect the bearer token as a root-level administrator credential.
- Do not commit .env, .tower-token, kubeconfig, join tokens, certificates or private keys.
- Restrict Linux and Kubernetes administrator access.
- Use immutable image tags or digests where supported.
- Keep Applications private unless public ingress is required.
- Review TLS Secret type and namespace before creating an Ingress.
- Do not assume namespace separation alone is a hostile-tenant security boundary.
- Do not grant AI agents unrestricted shell, Kubernetes, Docker or hypervisor access.

## Monitoring and observability

The repository includes a VictoriaMetrics Compose service and optional observability/cost foundations. Resource lifecycle events and operation history are available from the Control Tower persistence model.

A production observability design should correlate user/tenant identity, API request ID, resource ID and generation, operation ID and driver, Kubernetes object UID and namespace, application logs/metrics/traces, model request ID and model/prompt version, MCP server/tool name and version, and cost/resource utilisation.

Do not claim full distributed tracing or end-to-end AI interaction traceability unless the deployed version has been tested to emit and retain those records.

## Backup and restore

The Control Tower job/resource database currently defaults to:

    control-tower/var/jobs.sqlite3

For a simple offline copy:

    sudo systemctl stop gamgee-control-tower
    sudo cp control-tower/var/jobs.sqlite3 /secure-backup-location/jobs.sqlite3
    sudo systemctl start gamgee-control-tower

This is a Control Tower database backup only. It is not a backup of K3s state, application data, persistent volumes, container registries, AI Factory PostgreSQL, Qdrant, NATS or Ollama model data.

A complete recovery design must identify every state store, its backup method, encryption keys, retention, consistency requirements and restoration order. Test restore—not merely backup creation.

## AI Factory state

The AI Factory Compose profile persists data in named volumes for NATS, Qdrant, PostgreSQL and Ollama. Use database-native backup methods for PostgreSQL and verify the Qdrant/NATS backup approach for the selected version. Keep encryption keys and credentials separate from backup media.

Running docker compose down preserves named volumes. Running docker compose down -v removes them and can destroy state.

## Upgrade procedure

1. Read release notes and inspect changed Compose tags, requirements and Ansible roles.
2. Identify API/schema migrations and CRD compatibility.
3. Back up all state stores.
4. Test the upgrade in a disposable clone.
5. Confirm rollback feasibility.
6. Schedule a maintenance window.
7. Upgrade one layer at a time where dependencies permit.
8. Run smoke tests for Control Tower, node readiness, Workspace creation, Application lifecycle and Function invocation.
9. Verify metrics, logs and event flow.
10. Record the deployed version matrix.

## Incident response

1. Establish scope: cell, node, workspace, resource, service or AI workflow.
2. Preserve relevant logs and operation history.
3. Avoid destructive retries while system state is unknown.
4. Check desired state against observed state.
5. Identify whether the issue is API, reconciliation, driver, Kubernetes, host, network, storage or dependency related.
6. Apply the smallest reversible remediation.
7. Verify the resource and dependent services.
8. Record root cause, impact and follow-up actions.

## Production-readiness gates

Before production use, close and test:

- Multi-user OIDC, MFA, RBAC and workspace-scoped authorisation.
- Split unprivileged API and narrow privileged executor.
- Shared durable state, distributed worker coordination and Control Tower HA.
- K3s control-plane HA and API endpoint failover.
- Enforced NetworkPolicy with tenant-isolation tests.
- Secret management and rotation.
- Signed image/artifact verification.
- Backups and full restore drills.
- Central logs, metrics, traces, alerting and runbooks.
- Real-cluster end-to-end acceptance tests.
- AI model/tool governance, tenant-aware retrieval and audit correlation.
- MCP allowlisting, authentication, least privilege and tool-level authorisation.
- Capacity, performance, failure-injection and security testing.

## Operational principle

GAMGEE should make routine operations simpler, not hide failure. Every managed action must have an identity, a scope, a desired state, an observed outcome and a recovery path. If a feature cannot explain what it changed and how to reverse or recover it, it is not ready to be treated as a mature platform capability.
