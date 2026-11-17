# GAMGEE Cloud OS — Architecture and Product Contract

## 1. Product objective

GAMGEE is an outcome-oriented cloud operating environment, not a rebranded infrastructure dashboard. It starts as a single self-contained cell on one Linux VM and can add worker compute without changing the user-facing resource model.

The primary operator workflow is:

1. Install GAMGEE.
2. Create a workspace.
3. Launch an application or service.
4. Add compute when capacity is required.
5. Observe and operate the created resources from the same control surface.

Internal infrastructure profiles are implementation bundles. They are not user-facing cloud products.

## 2. Design principles

- **Small vocabulary:** Cell, Workspace, Application, Function, Machine, Service, Route and Secret.
- **Progressive disclosure:** normal path first, advanced capability catalogue only when needed.
- **Intent over configuration:** users provide business/runtime intent; infrastructure settings are derived from guarded defaults.
- **Typed contracts:** schemas and policy are the only way to express supported operations.
- **Deterministic compiler:** user and AI input compiles to a bounded resource model, never arbitrary shell or arbitrary Kubernetes YAML.
- **Reconciliation:** desired resources should eventually converge to observed infrastructure; the current release has deterministic create/scale/restart/delete operations and an initial manifest adapter, with continuous reconciliation still to be built.
- **Replaceable engines:** upstream tools own their technical domains; GAMGEE owns the stable user/resource contract.
- **Safety before autonomy:** identity, quota, policy, approval, idempotency and audit precede autonomous changes.
- **Progressive scale:** one node -> one server plus workers -> multiple HA servers -> independent cells/federation.

## 3. Logical architecture

    User Experience
      - white web workspace
      - prompt and visual design modes (planned)
      - service/blueprint catalogue
               |
    GAMGEE Intent API
      - typed Workspace/Application requests
      - future Function/Machine/Service contracts
               |
    Policy + Plan + Approval + Audit
      - schema validation
      - tenant boundary
      - quotas and policy checks
      - plan hash / approvals
               |
    Control Plane
      - operation/job state
      - resource store
      - reconcilers (incremental implementation)
      - driver registry
         /          |           \
    Runtime      Machine      Managed Service
    Driver       Driver        Driver
      |            |              |
    K3s        Incus/KVM     Upstream operators
      |
    K3s Server + Agent Nodes

    Cross-cutting: Identity, Secrets, Security, Network, Observability,
    FinOps, Backup, Governance and AI Gateway.

## 4. Stable product concepts

| Resource | User meaning | Intended driver |
| --- | --- | --- |
| Cell | One managed cloud boundary/site | K3s/control plane; later cell registry and federation |
| Workspace | Customer/team/project boundary | Capsule tenant adapter initially |
| Application | Long-running container application | K3s Deployment/Service/Ingress |
| Function | Event- or request-triggered execution | KEDA + WASM/OCI runtime (planned) |
| Machine | Full virtual machine | Incus/KVM first |
| Service | Managed database, queue, cache, vector store, object store, etc. | Upstream chart/operator |
| Route | Private or public network exposure | Traefik/Gateway API |
| Secret | Credential or key reference | OpenBao (planned) |
| Policy | Rules for identity, network, workload and data | OIDC/RBAC, CNI policy, Kyverno |

The GAMGEE object and lifecycle contract must outlive any single driver implementation.

## 5. Current reference implementation

### Control Tower

The FastAPI Control Tower is the initial local control plane. It exposes a white UI and token-protected APIs for profile planning, installation jobs, cluster status, workspace creation and stateless applications.

It is intentionally loopback-bound and run as root in the current installer because installation jobs manage host services. This is not an appropriate exposed multi-user control plane. OIDC, delegated roles and a least-privilege separated executor are required before broader exposure.

### Cluster adapter

The Kubernetes adapter invokes a fixed kubectl executable using subprocess argument arrays, without a shell. It accepts typed request models and compiles resources from a fixed set of templates.

Current application resources:
- Deployment
- ClusterIP Service
- Optional Traefik Ingress with pre-existing TLS Secret

It does not accept arbitrary manifests, commands, host paths, privileged containers, custom capabilities or ServiceAccount token mounting.

### Tenant adapter

Workspace creation compiles a Capsule Tenant with:
- production and staging namespaces
- quota limits
- gamgee.cloud/tenant metadata label
- Capsule owner reference

An identity account and actual login flow are not created by this adapter.

### Installation adapter

The Control Tower resolves dependencies, preflights host resources, fingerprints source playbooks and executes only repository-owned Ansible playbooks. Operations are serialized. Failed jobs are recorded; destructive automatic rollback is deliberately not performed.

### Compute adapter

K3s workers can join a single K3s server with the secure prompt-based join script. The existing single server remains a control-plane single point of failure.

## 6. Progressive scale model

### Stage 1 — Personal cell

One Linux VM runs:
- Control Tower
- K3s server
- Capsule
- Traefik
- OpenCost
- small application workloads

### Stage 2 — Expanded cell

One K3s server plus multiple K3s agents. The scheduler distributes replicas and new Pods across available nodes subject to resources and constraints.

The user-facing Workspace and Application contracts remain unchanged.

### Stage 3 — Resilient cell

Future target:
- three K3s server nodes using embedded etcd
- stable API endpoint/load balancer
- control-plane backup and restore
- tested quorum, upgrade and failure-recovery procedures
- enforced network policies
- replicated storage where required

This stage is not delivered by the current bootstrap. Merely adding worker nodes does not provide control-plane HA.

### Stage 4 — Multiple cells

Future target:
- independent cells at sites or cloud boundaries
- central organisation/identity and policy layer
- local site control planes continue to operate during WAN disconnection
- federated telemetry and central inventory
- workload placement and data residency policies
- hybrid cloud adapters

Do not turn multiple cells into one fragile global Kubernetes cluster. Federation should coordinate intent and policy while preserving local failure boundaries.

## 7. Security invariants

1. No AI-generated arbitrary shell.
2. No arbitrary YAML passed through the public API.
3. Every resource name is validated before it enters a command or manifest.
4. Public exposure requires an explicit hostname and TLS Secret.
5. Application workloads run without privilege escalation, as non-root, with all capabilities dropped and a read-only root filesystem.
6. Tenant resources are checked before app operations.
7. Destructive operations are separate from stop/scale operations.
8. Administration interface is not publicly exposed.
9. All operations should become auditable and attributable to an authenticated identity.
10. Network policy is not considered effective until the chosen CNI actually enforces it.

Current known gaps are documented in the root README. The most important pre-production blockers are per-user identity/authorization, enforcing network segmentation, secret lifecycle, backup/restore and HA.

## 8. Data and state ownership

- Control Tower SQLite: installation job history only.
- Kubernetes API: live workspace and application objects in the current release.
- Capsule: tenant namespace and quota ownership.
- Application image registry: immutable/versioned image artifact source.
- AI Factory stores: Qdrant, Postgres, NATS and configured model data.

A future GAMGEE resource store must distinguish:
- desired resource intent
- observed infrastructure state
- operation history
- audit events
- secret references (never plaintext secret values)

## 9. Delivery gates

A feature is not complete because a YAML or Helm values file exists. A capability is "implemented" only when:
- a user can request it through the product experience/API
- validation and authorization are enforced
- the adapter applies the resource safely and idempotently
- observed status is returned
- stop/update/delete semantics are documented
- tests cover success, rejection and failure paths
- a disposable environment test proves the integration
- operational recovery is documented

This definition prevents architecture diagrams and installer scaffolds from being mistaken for working cloud features.
