# 07 — API, Integrations and MCP

## Integration philosophy

GAMGEE integrations preserve the platform principle: callers express a typed outcome through a stable contract; GAMGEE owns translation into infrastructure-specific actions.

Use the GAMGEE API for resource lifecycle operations. Use Kubernetes APIs only for supported operator tasks and diagnostics. Use MCP to expose explicitly approved tools to AI workflows—not as a replacement for authentication, authorisation or the resource API.

## Resource API model

The current resource envelope is versioned as gamgee.io/v1alpha1. The Resource Engine currently registers Application and Function drivers. Workspace creation is exposed through Control Tower APIs but is not yet a generic Resource Engine driver.

The API model includes resource identity and kind, workspace and name, typed desired specification, desired and observed state, generation and observed generation, lifecycle phase and last error, generation-scoped operation records, idempotency mapping and lifecycle events.

Current phases include pending, reconciling, progressing, ready, error, deleting and deleted.

## API discovery and authentication

The Control Tower requires an installation-level bearer token for /v1/tower/* endpoints:

    Authorization: Bearer <token>

The token is a privileged administration credential. Store it in a secret manager or protected file. Never expose it in browser JavaScript, source control, logs or a public client application.

Discover the exact OpenAPI contract from the running service and inspect resource models in control-tower/tower/resource_models.py. The API schema is authoritative for field names, required fields, enum values and response structures. Do not invent endpoint paths or payloads from this conceptual guide.

## Idempotency and reconciliation

For mutation calls that support it, send an Idempotency-Key header. The current implementation scopes the key to a workspace and binds it to a canonical request hash. Reusing the same key with a different payload returns HTTP 409.

An accepted request does not necessarily mean the workload is ready. A client should submit the request, capture resource and operation identifiers, poll/read status through the supported interface, wait for ready/error/terminal state, display observed state and last error, and retry only according to the idempotency contract.

Do not submit duplicate creates merely because a deployment takes time to converge.

## Resource kinds and maturity

| Kind | Purpose | Current maturity |
|---|---|---|
| Workspace | Tenant boundary and namespaces | Workspace API exists; Capsule-backed |
| Application | Stateless OCI workload | Implemented |
| Function | Spin/WASM HTTP function | First HTTP/WASM slice implemented |
| Machine | VM lifecycle | Planned Incus/QEMU/KVM adapter |
| Service | Managed database, messaging, storage or AI | Planned |
| Route | Routing abstraction | Planned |
| Secret | Secret lifecycle adapter | Planned |

## Integration patterns

### Synchronous API
Use direct API calls for short request/response operations. Apply timeouts, retries with backoff and idempotency keys.

### Asynchronous integration
Use event transport for long-running or event-driven work. AI Factory includes NATS JetStream and an integration worker. Define event schemas, versioning, delivery semantics, deduplication and dead-letter handling before relying on it for business-critical integration.

### Event consumers
Consumers should be idempotent. Validate event version and tenant scope, persist checkpoints and do not assume exactly-once delivery.

### Provider adapters
Integrate external infrastructure behind a driver or adapter boundary. Do not leak provider-specific fields into the canonical resource model unless they are part of the user outcome.

## MCP in GAMGEE

Model Context Protocol (MCP) provides a standard way for an AI client to discover and invoke tools exposed by an MCP server. GAMGEE AI Factory accepts an MCP_SERVERS configuration value containing a JSON mapping of logical names to Streamable HTTP MCP endpoints.

Example:

    MCP_SERVERS={"inventory":"http://mcp-inventory:8000/mcp"}

This is an illustrative private-network endpoint. Replace it with an endpoint you operate and trust. The configured server list is an allowlist boundary; do not accept arbitrary MCP URLs from end users.

## Recommended MCP architecture

    User / Developer
          |
          v
    GAMGEE Workspace / AI client
          |
          v
    AI Factory authentication and tenant context
          |
          v
    Agent workflow (LangGraph)
          |
          v
    Tool policy and MCP allowlist
          |
          +------> Read-only documentation MCP
          |
          +------> Approved business-system MCP
          |
          +------> GAMGEE Resource API adapter
                         |
                         v
                  Schema + policy validation
                         |
                         v
                  Approval / Resource Engine
                         |
                         v
                  Registered resource driver

The AI model should not connect directly to Kubernetes API, host SSH, Docker socket, hypervisor root API or unrestricted database administrator credentials.

## MCP tool design

Prefer small, purpose-specific tools with explicit schemas. Future GAMGEE-oriented examples:

- list_workspaces — read-only list constrained to caller scope.
- get_resource_status — return desired and observed state.
- get_operation_history — retrieve outcomes with sensitive fields redacted.
- propose_application — validate and return a proposal without applying it.
- request_scale_change — create an approval-bound scale proposal.
- get_capacity_summary — return aggregated, authorised capacity information.

These names are design examples, not claims that the tools already exist.

Each tool should define authentication and identity propagation, tenant scope, strict input/output schema, read/write classification, rate/time/payload limits, audit and correlation ID, approval requirements, error contract and retry semantics.

## MCP security checklist

- Allowlist server endpoints and validate TLS certificates.
- Do not allow user-controlled server URL injection.
- Authenticate MCP client and server where supported.
- Use least-privilege credentials per server/tool.
- Enforce tenant scope in the tool implementation, not only in the prompt.
- Treat tool descriptions, results and retrieved content as untrusted.
- Require confirmation for destructive or externally visible actions.
- Redact credentials and sensitive data from logs.
- Apply network egress restrictions and timeouts.
- Rotate credentials and review tool permissions periodically.

## Example end-to-end interaction

A developer asks: "Deploy the customer API privately with 1 CPU and 1 GiB memory."

The AI workflow should authenticate the developer and resolve the authorised Workspace, interpret the request into an Application proposal, validate image/resource/exposure values, ask for missing fields, show the proposed change and impact, submit only after approval through the GAMGEE Resource API, record resource and operation IDs, read status until ready/error, and return the result.

The model does not create Kubernetes YAML, execute shell commands or bypass the API.

## API evolution

The gamgee.io/v1alpha1 contract is an early version. Future versions should document compatibility, deprecation windows, pagination, filtering, error formats, rate limits, audit semantics and generated client SDKs. Breaking changes must be explicit and tested against API consumers.
