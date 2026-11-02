# GAMGEE-CLOUD

Tiny, small, but most brilliant cloud out there. AI-powered somemore!

Gamgee Cloud is a single-VM, multi-tenant cloud PaaS built on K3s, with two AI components:

- **AI control plane** (`ai-agent/`): turns a natural-language request into a Capsule tenant on the cluster.
- **AI Factory** (`ai-factory/`): a private, on-premise AI workspace with RAG, agent workflows, tool integration, and an event bus.

> **Status:** early-stage. The cluster bootstrap, tenant provisioning, and AI Factory are implemented and unit-tested. The authentication, billing, and log-shipping configs under `platform/` are starting points that nothing deploys automatically yet (see [Platform components](#platform-components)).

## Contents

- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Platform components](#platform-components)
- [AI control plane](#ai-control-plane)
- [AI Factory](#ai-factory)
- [Development](#development)
- [Security notes](#security-notes)
- [Teardown](#teardown)

## Architecture

```
                       Traefik (ingress, TLS, rate limit)
                                   │
        ┌──────────────────────────┴──────────────────────────┐
        │                                                     │
  AI control plane (ai-agent)                         AI Factory (ai-factory)
  POST /v1/cloud/execute                              RAG · LangGraph · MCP · NATS
        │ creates                                              │ self-hosted models
        ▼                                                      ▼
  Capsule Tenant ─► namespaces + quotas + network policies   Ollama / Qdrant / Postgres
        │
  K3s (single VM) · OpenCost (cost) · Authentik / Lago / Vector (optional, see below)
```

Each tenant gets a `<name>-production` and `<name>-staging` namespace with resource quotas, and is isolated from other tenants by network policy.

## Repository layout

| Path | Purpose |
| --- | --- |
| `scripts/bootstrap.sh` | Installs K3s, Helm, Capsule, Traefik, and OpenCost on a Debian/Ubuntu host. |
| `scripts/teardown.sh` | Removes K3s and every workload on the host (asks for confirmation). |
| `ai-agent/` | FastAPI control plane that provisions tenants from natural language. |
| `ai-factory/` | FastAPI AI Factory service, workspace UI, and integration worker. |
| `platform/capsule/` | Tenant and network-policy templates (use `envsubst`). |
| `platform/traefik/` | Example IngressRoute and rate-limit middlewares. |
| `platform/authentik/`, `platform/lago/`, `platform/observability/` | Helm values, billing events, and Vector config for identity, metering, and logs. |
| `docker-compose.yml` | Local Authentik and VictoriaMetrics stack. |
| `docker-compose.ai-factory.yml` | AI Factory stack: Qdrant, Postgres, NATS, Ollama, worker. |
| `Makefile` | Shortcuts for bootstrap, tests, and the AI Factory stack. |

## Requirements

| For | You need |
| --- | --- |
| Cluster (PaaS) | A Debian or Ubuntu VM with `sudo`/root and internet access. Bootstrap installs `curl`, `git`, K3s, and Helm. |
| Local stacks | Docker with the Compose plugin. |
| AI services (development) | Python 3.12. |
| AI Factory models | Enough RAM/VRAM for the model you choose (`llama3.1:8b` needs roughly 8 GB). A GPU is optional. |

## Quick start

### 1. Bootstrap the cluster

```bash
git clone https://github.com/roime81/GAMGEE-CLOUD.git
cd GAMGEE-CLOUD
make bootstrap
```

This installs K3s (with its bundled Traefik disabled), then installs Capsule, Traefik (as the default ingress class), and OpenCost with Helm. The kubeconfig is placed at `~/.kube/config` (or `/etc/rancher/k3s/k3s.yaml` when run as root). Verify:

```bash
kubectl get pods -A
```

### 2. Create a tenant

Either use the AI control plane (below) or apply the template directly:

```bash
export TENANT_NAME=acme TENANT_OWNER_EMAIL=owner@example.com
envsubst < platform/capsule/tenant-template.yaml | kubectl apply -f -
```

Then apply the default-deny network policies to each of the tenant's namespaces:

```bash
for ns in acme-production acme-staging; do
  TENANT_NAMESPACE=$ns envsubst < platform/capsule/network-policies.yaml | kubectl apply -f -
done
```

> `make deploy-core` applies `network-policies.yaml` without substituting `${TENANT_NAMESPACE}`, so it will not work as-is; use the loop above.

### 3. Start the AI services

See [AI control plane](#ai-control-plane) and [AI Factory](#ai-factory).

## Platform components

| Component | Status | Notes |
| --- | --- | --- |
| K3s | Installed by `make bootstrap` | Single-node cluster; bundled Traefik disabled. |
| Capsule | Installed by `make bootstrap` | Multi-tenancy: tenants, namespaces, quotas. |
| Traefik | Installed by `make bootstrap` | `platform/traefik/` has example routes. Edit the host, service, and `certResolver` before applying; the example expects a resolver named `letsencrypt` that you must configure. |
| OpenCost | Installed by `make bootstrap` | Per-namespace cost visibility. |
| Authentik | Compose file and Helm values provided, not installed by bootstrap | `docker-compose.yml` runs Authentik, Postgres, Redis, and VictoriaMetrics locally. Requires `AUTHENTIK_SECRET_KEY` and `AUTHENTIK_POSTGRES_PASSWORD` in `.env`. |
| Lago | Event definitions only | `platform/lago/billing-config.yaml` defines `compute_instance_hours` and `persistent_storage_gib_hours`. Lago itself is not deployed, and nothing emits these events yet. |
| Vector | Config only | `platform/observability/vector-config.toml` ships Kubernetes logs to stdout and exposes metrics on `:9598`. It is not deployed by bootstrap. |

To run the local Authentik and metrics stack:

```bash
cp .env.example .env     # set every replace-with-… value
docker compose up -d
```

Authentik is at <http://127.0.0.1:9000> and VictoriaMetrics at <http://127.0.0.1:8428> (bound to localhost only).

## AI control plane

`ai-agent/` accepts a natural-language request and creates a Capsule tenant. The language model only *classifies* whether the request is a provisioning action; it never writes Kubernetes configuration. The tenant manifest is built from validated fields in code.

### Run it

```bash
cd ai-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export GAMGEE_API_TOKEN="$(openssl rand -hex 32)"
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

(`make run-agent` runs the last step.) It needs a working kubeconfig (or in-cluster credentials) with permission to create Capsule `Tenant` resources.

### Use it

```bash
curl -X POST http://127.0.0.1:8080/v1/cloud/execute \
  -H "Authorization: Bearer $GAMGEE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"Create a workspace for the acme team","tenant_name":"acme","owner_email":"owner@example.com"}'
```

| Field | Rules |
| --- | --- |
| `prompt` | 1–2000 characters; must describe a provisioning action. |
| `tenant_name` | Lowercase DNS label (`a-z`, `0-9`, `-`, at most 63 characters). |
| `owner_email` | Valid email address; becomes the Capsule tenant owner. |

Responses: `200` with the generated manifest; `401` bad token; `409` tenant exists; `422` invalid or unsupported request; `503` token not configured, model, or Kubernetes unavailable. `GET /healthz` is the liveness probe.

### Intent classification

| `GAMGEE_LLM_MODEL` | Behavior |
| --- | --- |
| unset (default) | Deterministic keyword matching (`tenant`, `workspace`, `deploy`, `provision`, `create`, …). No model needed. |
| set (any [LiteLLM](https://docs.litellm.ai/) model string, e.g. `ollama/llama3.1`) | The model classifies the request and must return strict JSON. Provide that provider's credentials as environment variables. |

### Default tenant quotas

4 CPU / 8 GiB requested, 8 CPU / 16 GiB limit, 30 pods, 10 services, 5 persistent volume claims. Change them in `ai-agent/app/validator.py` (and `platform/capsule/tenant-template.yaml` for manual use).

### Container

```bash
docker build -t gamgee-agent ai-agent
docker run --rm -p 127.0.0.1:8080:8080 -e GAMGEE_API_TOKEN=... -v ~/.kube:/kube:ro -e KUBECONFIG=/kube/config gamgee-agent
```

The image runs as a non-root user. Inside the cluster it uses in-cluster credentials; grant its ServiceAccount access to `tenants.capsule.clastix.io`.

## AI Factory

Besides the cloud PaaS, Gamgee Cloud ships a **private, on-premise AI Factory** (`ai-factory/`): a prompt-first workspace backed by RAG, a LangGraph orchestrator, an MCP tool gateway, and an event bus, all running against self-hosted models so data stays on your infrastructure.

```
Browser (prompt-first UI, SSE) ──► Ingress (Traefik / rate limit / TLS)
        └─► ai-factory (FastAPI)
              ├─ LangGraph supervisor ─► knowledge (RAG) | tool (MCP) | event (NATS) | chat
              ├─ Qdrant        hybrid retrieval (dense + BM25 re-rank), tenant-filtered
              ├─ PostgreSQL    LangGraph checkpoints (per-thread state)
              ├─ MCP servers   allowlisted Streamable-HTTP tool servers
              ├─ NATS JetStream ◄─ integration-worker (legacy/file/DB adapters publish events)
              └─ Ollama        self-hosted LLM + embeddings (any OpenAI-compatible endpoint)
```

### Quick start

```bash
cp .env.example .env        # set every replace-with-… value, including GAMGEE_API_TOKEN
make factory-up             # builds and starts the stack
docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml exec ollama ollama pull llama3.1:8b
docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml exec ollama ollama pull nomic-embed-text
```

Open <http://127.0.0.1:8090>, paste your `GAMGEE_API_TOKEN` into the token field, and ask a question. Ports are bound to `127.0.0.1`; put Traefik in front (see `platform/traefik/ai-factory-route.yaml`) to expose it with TLS.

Ingest knowledge, then query it:

```bash
curl -X POST http://127.0.0.1:8090/v1/factory/ingest \
  -H "Authorization: Bearer $GAMGEE_API_TOKEN" -H "X-Gamgee-Tenant: acme" \
  -H "Content-Type: application/json" \
  -d '{"source":"handbook.md","text":"…document text…"}'

curl -N -X POST http://127.0.0.1:8090/v1/factory/chat \
  -H "Authorization: Bearer $GAMGEE_API_TOKEN" -H "X-Gamgee-Tenant: acme" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What does the handbook say about onboarding?"}'
```

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Prompt-first workspace UI. |
| `POST /v1/factory/chat` | Runs the graph and streams SSE events: `thread`, `step`, `sources`, `token`, `done`, `error`. Body: `prompt`, optional `mode` (`auto`, `chat`, `knowledge`, `tool`, `event`), `tool` (`server`, `name`, `arguments`), `event_subject`, `thread_id`. |
| `POST /v1/factory/ingest` | Chunks, embeds, and stores a document for the tenant. |
| `GET /v1/factory/tools` | Lists tools on every configured MCP server. |
| `GET /healthz` | Liveness probe. |

All `/v1` endpoints need `Authorization: Bearer $GAMGEE_API_TOKEN`; the tenant comes from `X-Gamgee-Tenant` (default `default`) and scopes retrieval and event subjects.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL` / `LLM_MODEL` / `EMBEDDING_MODEL` | Ollama, `llama3.1:8b`, `nomic-embed-text` | OpenAI-compatible endpoint serving both chat and embeddings. Point it at vLLM or llama.cpp instead if it serves both models. |
| `MCP_SERVERS` | empty | JSON `{"name": "http(s)://…/mcp"}`. This is the allowlist; tool calls to other servers are rejected. |
| `POSTGRES_CHECKPOINT_URL` | unset | Persists graph state in Postgres; without it, state is in memory. |
| `NATS_URL` | unset | Enables the `event` route. |

Run the tests with `make test-factory`.

### Current scope and limitations

- Tools are invoked explicitly (`tool` in the request); the model does not yet choose tools on its own, and only Streamable-HTTP MCP servers are supported (no stdio).
- Retrieval is dense search with a BM25 re-rank over the top candidates, not a cross-encoder or a full sparse index.
- Document ingestion is via the API (text); folder/file watchers and legacy adapters should publish to NATS (`gamgee.<tenant>.<subject>`), which the integration worker replays as graph runs.
- Authentication is a single shared bearer token, and the tenant header is trusted. Put an OIDC-aware gateway (e.g. Authentik via Traefik forward-auth) in front and have it set `X-Gamgee-Tenant` before exposing this to multiple tenants.
- The Postgres checkpointer, NATS worker, and Ollama stack were not exercised end-to-end in CI; unit tests cover the API, RAG, MCP handshake, and routing with mocked backends.

## Development

```bash
make test           # AI control plane unit tests
make test-factory   # AI Factory unit tests (needs ai-factory/requirements.txt installed)
make factory-up     # start the AI Factory stack
make factory-down   # stop it
```

Set up a virtual environment per service (`ai-agent/requirements.txt`, `ai-factory/requirements.txt`) before running tests. There is no CI workflow in this repository yet.

## Security notes

- Never commit `.env`; it is git-ignored. Replace every `replace-with-…` placeholder with a long random value.
- `GAMGEE_API_TOKEN` is a single shared secret. Both services refuse requests when it is unset.
- Compose ports are bound to `127.0.0.1`. Expose services only through Traefik with TLS.
- The control plane can create cluster-scoped Capsule tenants. Run it with a least-privilege ServiceAccount and keep it off the public internet.
- The tenant isolation provided by Capsule and the network policies applies to Kubernetes workloads. The AI Factory's tenant separation is a retrieval filter on a shared Qdrant collection and relies on a trusted gateway setting `X-Gamgee-Tenant`.

## Teardown

```bash
make clean        # prompts for confirmation
bash scripts/teardown.sh --yes   # non-interactive
```

This uninstalls K3s and **deletes every workload and volume in the cluster** on this host. To remove the Compose stacks and their data, run `make factory-down` and `docker compose down -v`.
