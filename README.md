# GAMGEE-CLOUD
Tiny, Small but most brilliant cloud out there. AI-powered somemore !

### AI Factory

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

#### Quick start

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

#### API

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Prompt-first workspace UI. |
| `POST /v1/factory/chat` | Runs the graph and streams SSE events: `thread`, `step`, `sources`, `token`, `done`, `error`. Body: `prompt`, optional `mode` (`auto`, `chat`, `knowledge`, `tool`, `event`), `tool` (`server`, `name`, `arguments`), `event_subject`, `thread_id`. |
| `POST /v1/factory/ingest` | Chunks, embeds, and stores a document for the tenant. |
| `GET /v1/factory/tools` | Lists tools on every configured MCP server. |
| `GET /healthz` | Liveness probe. |

All `/v1` endpoints need `Authorization: Bearer $GAMGEE_API_TOKEN`; the tenant comes from `X-Gamgee-Tenant` (default `default`) and scopes retrieval and event subjects.

#### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LLM_BASE_URL` / `LLM_MODEL` / `EMBEDDING_MODEL` | Ollama, `llama3.1:8b`, `nomic-embed-text` | OpenAI-compatible endpoint serving both chat and embeddings. Point it at vLLM or llama.cpp instead if it serves both models. |
| `MCP_SERVERS` | empty | JSON `{"name": "http(s)://…/mcp"}`. This is the allowlist; tool calls to other servers are rejected. |
| `POSTGRES_CHECKPOINT_URL` | unset | Persists graph state in Postgres; without it, state is in memory. |
| `NATS_URL` | unset | Enables the `event` route. |

Run the tests with `make test-factory`.

#### Current scope and limitations

- Tools are invoked explicitly (`tool` in the request); the model does not yet choose tools on its own, and only Streamable-HTTP MCP servers are supported (no stdio).
- Retrieval is dense search with a BM25 re-rank over the top candidates, not a cross-encoder or a full sparse index.
- Document ingestion is via the API (text); folder/file watchers and legacy adapters should publish to NATS (`gamgee.<tenant>.<subject>`), which the integration worker replays as graph runs.
- Authentication is a single shared bearer token, and the tenant header is trusted. Put an OIDC-aware gateway (e.g. Authentik via Traefik forward-auth) in front and have it set `X-Gamgee-Tenant` before exposing this to multiple tenants.
- The Postgres checkpointer, NATS worker, and Ollama stack were not exercised end-to-end in CI; unit tests cover the API, RAG, MCP handshake, and routing with mocked backends.
