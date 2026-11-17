# 03 — Technology Stack

This chapter records technologies visible in the repository and their responsibilities. Exact versions are controlled by requirements files, Compose files and Ansible playbooks; inspect those sources before upgrading.

## Stack at a glance

| Layer | Technology | Responsibility | Status |
|---|---|---|---|
| Control Tower API | Python, FastAPI, Uvicorn | HTTP API and UI backend | Implemented |
| Resource schemas | Typed/Pydantic models | Validate GAMGEE resource intent | Implemented |
| Resource state | SQLite, WAL, transactions | Job ledger and desired/observed state | Implemented; single-host boundary |
| Container orchestration | K3s / Kubernetes | Schedules Application workloads | Implemented |
| Tenant isolation | Capsule | Tenant namespaces and quotas | Implemented foundation |
| Ingress | Traefik | HTTP(S) routing | Profile-dependent |
| Function runtime | SpinKube, Spin Operator, containerd Spin shim | Executes Spin-compatible WASM artifacts | First HTTP slice |
| Function autoscaling | KEDA HTTP Add-on | HTTP concurrency scale-to-zero/scale-up | First HTTP slice |
| Installation | Ansible, shell, systemd | Host and platform automation | Implemented |
| AI service API | FastAPI, Uvicorn | AI Factory HTTP entry point | Foundation |
| AI orchestration | LangGraph | Stateful agent/workflow orchestration | Foundation |
| Model abstraction | LiteLLM in ai-agent; OpenAI-compatible endpoint configuration in AI Factory | Model/provider abstraction | Component-dependent |
| Local inference | Ollama | Self-hosted model serving | Optional Compose profile |
| Retrieval | Qdrant | Vector storage for RAG | AI Factory profile |
| Embeddings | Configurable model; default nomic-embed-text | Text embeddings | Configurable |
| Workflow state | PostgreSQL 16 | LangGraph checkpoint persistence | AI Factory profile |
| Event integration | NATS JetStream | Asynchronous messaging foundation | AI Factory profile |
| Tool integration | MCP over Streamable HTTP | Approved external tools/context | Configurable foundation |
| Identity | Authentik Compose services; optional OIDC validation in AI Factory | Identity foundation | Full GAMGEE RBAC incomplete |
| Metrics | VictoriaMetrics | Metrics storage | Compose foundation |
| Cost visibility | OpenCost profile | Kubernetes cost visibility | Profile-dependent |
| Infrastructure provisioning | OpenTofu scaffold | Future provider provisioning | Scaffold, not complete workflow |

## Control plane

Control Tower uses Python and FastAPI. Uvicorn serves the application. Typed request models constrain resource fields. The Resource Engine is separated from API routes and delegates infrastructure changes to a driver registry.

SQLite is used for current resource/job persistence. WAL and transactional claiming support the single-host worker model. SQLite is not a distributed database and must not be treated as a shared state store for multiple Control Tower replicas.

## Workload plane

K3s provides a lightweight Kubernetes distribution for a cell. The server node hosts the control plane and system components; agents provide additional schedulable capacity. Capsule supplies tenant-oriented namespace boundaries and quotas. Traefik provides ingress routing where configured.

Applications are compiled into Kubernetes Deployment, Service and optional Ingress resources. The driver uses fixed, constrained compilation rather than accepting user-supplied Kubernetes manifests.

## Native Function runtime

The Function profile installs KEDA, cert-manager, Runtime Class Manager, the Spin containerd shim, Spin Operator and the KEDA HTTP Add-on. The Function driver compiles typed Function intent into SpinApp, InterceptorRoute, ScaledObject and supporting Kubernetes resources.

Only Spin-compatible WebAssembly OCI artifacts are accepted. A regular container image is not a valid Spin function artifact. The current Function path is HTTP-oriented; scheduled, queue and event triggers remain roadmap items.

## AI stack

The repository contains two AI-related services:

- **AI Agent service (ai-agent)** — a smaller FastAPI service with LiteLLM and Kubernetes client dependencies. It is separate from the AI Factory.
- **AI Factory (ai-factory)** — a workflow-oriented service using LangGraph, PostgreSQL checkpointing, Qdrant, NATS and a configurable OpenAI-compatible LLM endpoint. Its Compose overlay also defines Ollama for local inference.

The default Compose configuration points the AI Factory at Ollama using llama3.1:8b and uses nomic-embed-text as the embedding model. These defaults are not a benchmarked production sizing recommendation.

## Why this stack?

The stack favours widely understood components with clear boundaries. Python/FastAPI keeps the control API approachable; K3s provides a compact Kubernetes substrate; Ansible makes host installation repeatable; SQLite minimises first-install dependencies while limiting horizontal control-plane scale; LangGraph provides graph-oriented workflow orchestration; PostgreSQL persists checkpoints; Qdrant supports vector retrieval; NATS supports asynchronous integration; Ollama enables local inference.

The goal is not to expose these as separate products to every user. They remain implementation details behind GAMGEE capabilities.

## Version discipline

Before upgrading a component, review pinned versions, verify compatibility with K3s/Kubernetes and CRDs, test installation and rollback in a disposable environment, verify state migrations, run tests and record the tested version combination. A version pin alone is not a supported compatibility matrix.
