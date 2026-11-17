# 01 — Introduction and Product Philosophy

## What is GAMGEE?

GAMGEE is an outcome-oriented cloud operating layer. A user describes a desired outcome—run an application, expose a function, create an isolated workspace or eventually provision a machine—and GAMGEE translates that intent into controlled infrastructure actions.

The product contract is the GAMGEE resource model, not the implementation details of Kubernetes, K3s, Helm, Ansible, a hypervisor or a particular cloud provider.

GAMGEE accepts typed, versioned intent; validates requests against schemas and policy; records desired state and operation history; reconciles desired state through registered drivers; and reports observed state and lifecycle events. Execution engines can evolve without forcing users to learn new infrastructure interfaces.

GAMGEE is not currently a complete replacement for every public-cloud service, hypervisor or enterprise cloud management suite. It is an evolving platform with an implemented single-cell foundation and a broader target architecture.

## Motivation

Infrastructure has become powerful but fragmented. Teams need to understand virtualisation, container orchestration, networking, storage, identity, secrets, observability, automation and billing before delivering a simple application. This creates configuration overload, operational fragmentation, inconsistent safety, tight coupling to providers and AI workflows that may suggest actions without safe execution boundaries.

GAMGEE addresses these problems by keeping the user-facing model small and typed, while placing complexity behind the Control Tower, Resource Engine and resource drivers.

## Product principles

### Outcome over infrastructure
A developer asks for an Application with an image, resource bounds and exposure choice. The Application driver decides which Kubernetes objects are needed. The user does not submit raw Kubernetes YAML.

### Small surface area
Expose a few durable concepts—Cell, Workspace, Application, Function, Machine and Service—rather than every underlying component and flag. Not every concept is implemented yet.

### Deterministic execution
An accepted API request is not necessarily complete. The Resource Engine tracks reconciliation until observed state matches requested state or a meaningful error is reported.

### Replaceable engines
A resource contract should outlive its first driver. Kubernetes is the current execution engine for Applications and the first HTTP/WASM Function slice. Incus/QEMU/KVM and managed-service adapters are future directions.

### Secure defaults
Typed schemas, immutable artifact references, bounded resources, ownership labels and private-by-default exposure reduce risk. They do not replace a full enterprise security programme.

### AI assists; policy decides
AI can interpret requests, retrieve documentation, draft typed proposals and explain plans. It must not bypass schema validation, policy, approval or deterministic execution.

### Operational clarity
Create, update, stop, restart, delete and purge are different operations. Resource state, operation history, ownership and recovery semantics must be visible.

## What GAMGEE is—and is not

| GAMGEE is | GAMGEE is not |
|---|---|
| An intent-oriented cloud operating layer | A Kubernetes dashboard |
| A resource API with reconciliation | A wrapper around arbitrary shell commands |
| A unified experience over replaceable engines | A new hypervisor |
| A platform designed for private/hybrid evolution | A public cloud provider |
| A guarded foundation for AI-assisted operations | An unrestricted autonomous root agent |
| A cell-based architecture direction | A delivered multi-region federation service |

## Core vocabulary

| Term | Meaning |
|---|---|
| Control Tower | Management API, UI, installation workflow and operational entry point |
| Resource Engine | Persists desired state, schedules reconciliation and records observed state |
| Driver | Constrained adapter translating GAMGEE resources into engine-specific objects |
| Cell | GAMGEE cloud boundary; current shape is one Control Tower and one K3s cluster |
| Workspace | Tenant boundary with namespaces and quotas |
| Application | Stateless OCI-image workload managed by the Application driver |
| Function | Versioned Spin/WASM HTTP workload with KEDA-based scaling |
| Machine | Planned VM/compute abstraction over a hypervisor adapter |
| Service | Planned managed database, messaging, storage or AI service abstraction |
| AI Factory | Separate AI orchestration and integration services in this repository |
| MCP | Model Context Protocol for exposing tools/context to AI clients |

## Current product boundary

The current preview includes guided Control Tower installation, a single K3s server/control plane, worker joining, Capsule-backed workspaces, Application lifecycle management, a first SpinKube/KEDA HTTP Function path and a separate AI Factory Compose profile.

Important limitations:

- Control Tower uses SQLite and one in-process Resource Engine worker.
- Supported Control Tower deployment is one Uvicorn worker on one host.
- Worker nodes add workload capacity to the same cluster; they do not create control-plane HA.
- Machine lifecycle over Incus/QEMU/KVM is not yet a delivered Resource Engine driver.
- AI Factory is a separate deployable profile, not a fully integrated production AI control plane.
- MCP configuration is an integration boundary, not permission for arbitrary tool execution.

See [Architecture](architecture.md), [Compute and Scaling](compute-and-scaling.md) and [AI Factory](ai-factory.md).
