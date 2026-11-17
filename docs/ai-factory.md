# 06 — AI Factory and AI Usage

## Purpose

GAMGEE's AI direction is to make AI a managed cloud capability rather than an isolated chatbot. The repository contains an AI Agent service and a separate AI Factory profile. The AI Factory combines a model endpoint, workflow orchestration, persistent workflow state, vector retrieval, event transport and optional MCP tool connections.

This is an engineering foundation, not yet a fully integrated production AI control plane with complete model governance, tenant isolation, audit, policy and FinOps.

## AI Factory components

Start the Compose overlay with:

    docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml up -d --build

| Component | Responsibility |
|---|---|
| AI Factory API | FastAPI service on loopback port 8090 |
| Integration worker | Background event/integration worker |
| LangGraph | Stateful graph-based agent/workflow orchestration |
| PostgreSQL 16 | Workflow checkpoint persistence |
| Qdrant | Vector database for retrieval |
| NATS JetStream | Event transport and durable messaging foundation |
| Ollama | Optional self-hosted inference endpoint |
| LLM endpoint | Configurable OpenAI-compatible model API |
| Embedding model | Converts content and queries to vectors |
| MCP servers | Explicitly configured external tools/context endpoints |

The default model configuration is llama3.1:8b; the default embedding model is nomic-embed-text. These are configuration defaults, not guarantees that a model is downloaded, GPU accelerated or sized for production.

## Model serving and provider abstraction

The AI Factory reads LLM_BASE_URL and LLM_MODEL. The Compose default points to Ollama's OpenAI-compatible API endpoint inside the Compose network.

For another provider or gateway, configure the base URL and model name according to that provider's API compatibility and authentication requirements. Do not put provider secrets in source control. Use a secret store or protected environment file with restrictive permissions.

The separate ai-agent service pins LiteLLM in its requirements. Do not assume every AI Factory request is routed through LiteLLM; confirm the actual request path in the implementation before operating or troubleshooting it.

## How an AI request should work

The intended safe request lifecycle is:

1. Authenticate the caller and resolve tenant/workspace context.
2. Understand intent using the model; classify question, retrieval, proposal or action.
3. Retrieve approved context from documents or connected systems.
4. Plan a workflow with bounded steps and typed inputs.
5. Select tools only from an explicit allowlist and only when authorised.
6. Validate proposed actions against schemas, tenant scope and policy.
7. Request approval for privileged, destructive, costly or externally visible changes.
8. Execute through a deterministic API/driver boundary, never unrestricted host shell access.
9. Record request, model, prompt/template, tool, resource and operation metadata.
10. Return the result with references, status and limitations.

The full end-to-end governance and traceability lifecycle is a target requirement. Do not assume every stage is currently implemented.

## AI patterns and maturity

| Pattern | Description | Repository status |
|---|---|---|
| Model inference | Prompt a configured local/OpenAI-compatible endpoint | Configuration and service foundation |
| RAG | Retrieve relevant content from vector store | Qdrant and RAG module foundation |
| Agent workflow | Execute graph of bounded workflow steps | LangGraph orchestration foundation |
| Persistent workflow | Resume or inspect workflow state | PostgreSQL checkpoint integration |
| Event-driven integration | Trigger background work from events | NATS and worker foundation |
| MCP tool integration | Connect configured MCP endpoints | Configuration/client foundation; production governance required |
| Natural-language cloud operations | Convert prompt into typed resource proposal | Target capability; not unrestricted provisioning |
| Model catalogue/governance | Version, approve, evaluate and promote models | Planned |
| GPU scheduling/model autoscaling | Allocate accelerators and scale inference | Planned or externally operated today |

## Retrieval-augmented generation

A typical RAG pipeline:

1. Ingest approved source content.
2. Extract text and metadata.
3. Split content into chunks with source identifiers.
4. Generate embeddings with the configured embedding model.
5. Store vectors and metadata in Qdrant.
6. Embed the user query.
7. Retrieve relevant chunks.
8. Apply tenant, source and access filters.
9. Provide retrieved context to the model.
10. Return an answer with source references where available.

RAG is not a permission system. Retrieval filters must enforce caller access, and the source system remains authoritative. Never embed secrets or data a tenant is not allowed to access.

## Using AI to operate GAMGEE

A future GAMGEE assistant should explain why resources are not Ready, summarise operation failures, recommend deployment profiles based on measured capacity, draft Application/Function specifications, explain scale trade-offs, search approved documentation, prepare change plans and request operator approval.

Example prompt:

    Propose an Application deployment for the inventory API.
    Use the approved production workspace, keep it private,
    request 500m CPU and 512Mi memory, and show the plan
    before making any change.

Expected safe behaviour: return a typed proposal and impact summary. A human or explicitly authorised policy workflow approves it; the Resource Engine validates and reconciles it.

## Model selection and compute

Consider task complexity, quality, context window, latency, concurrency, data sensitivity, residency, hardware/VRAM, quantisation, embedding compatibility and cost per request. A small model may suit classification or extraction; complex reasoning may need a larger model. Benchmark representative prompts and concurrency rather than sizing from parameter count alone.

## AI security and governance

Production controls should include user authentication and tenant identity, tool-level allowlists and authorisation, prompt-injection defences, strict tool schemas, approvals for high-impact operations, network egress restrictions, protected credentials, telemetry redaction, model/prompt/tool version recording, token/time/concurrency/cost budgets and data-leakage tests.

A model response is never an authorisation decision. The API and policy layer must independently verify caller and operation.

## Start and stop the AI Factory

Prepare a protected environment file:

    cp .env.example .env
    chmod 600 .env

Set strong random values for GAMGEE_API_TOKEN, FACTORY_POSTGRES_PASSWORD, AUTHENTIK_POSTGRES_PASSWORD and AUTHENTIK_SECRET_KEY. Configure model and OIDC settings only after reviewing network and trust boundaries.

Start:

    docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml up -d --build

Inspect:

    docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml ps
    docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml logs -f ai-factory

Stop:

    docker compose -f docker-compose.yml -f docker-compose.ai-factory.yml down

Named volumes hold state. Do not add -v unless you deliberately intend to delete persistent Compose volumes and have verified backups.

## Current limitations

The Compose profile is a development and integration foundation, not a production HA topology. It does not by itself provide replicated PostgreSQL, Qdrant or NATS, GPU orchestration, model evaluation, complete per-tenant usage metering, centralised trace correlation or a complete MCP security gateway. Validate these controls before exposing AI services to production users.
