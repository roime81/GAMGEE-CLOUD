import hmac
import json
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from factory.config import Settings
from factory.events import EventBus
from factory.llm import LLMClient, LLMError
from factory.mcp import MCPError, MCPGateway
from factory.orchestrator import build_graph, build_messages, run_graph
from factory.rag import KnowledgeBase, VectorStoreError

_STATIC = Path(__file__).parent / "static"
_TENANT = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_bearer = HTTPBearer(auto_error=False)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=8000)
    mode: Literal["auto", "chat", "knowledge", "tool", "event"] = "auto"
    tool: ToolCall | None = None
    event_subject: str | None = Field(default=None, max_length=300)
    thread_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("prompt must not be blank")
        return value


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=1_000_000)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    kb: KnowledgeBase | None = None,
    mcp: MCPGateway | None = None,
    bus: EventBus | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    llm = llm or LLMClient(settings)
    kb = kb or KnowledgeBase(settings, llm)
    mcp = mcp or MCPGateway(settings)
    bus = bus or EventBus(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        checkpointer_cm = None
        checkpointer = None
        if settings.postgres_checkpoint_url:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_checkpoint_url)
            checkpointer = await checkpointer_cm.__aenter__()
            await checkpointer.setup()
        app.state.graph = build_graph(kb, mcp, bus, checkpointer)
        try:
            yield
        finally:
            if checkpointer_cm is not None:
                await checkpointer_cm.__aexit__(None, None, None)
            for closer in (llm, kb, mcp, bus):
                await closer.aclose()

    app = FastAPI(title="Gamgee Cloud AI Factory", version="1.0.0", lifespan=lifespan)

    def require_api_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        if not settings.api_token:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "The AI Factory is not configured.")
        if credentials is None or not hmac.compare_digest(
            credentials.credentials, settings.api_token
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "A valid bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def tenant_header(x_gamgee_tenant: str = Header(default="default")) -> str:
        if not _TENANT.fullmatch(x_gamgee_tenant):
            raise HTTPException(422, "X-Gamgee-Tenant must be a lowercase DNS label.")
        return x_gamgee_tenant

    auth = [Depends(require_api_token)]

    @app.get("/", include_in_schema=False)
    def workspace() -> FileResponse:
        return FileResponse(_STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    def health_check() -> dict:
        return {"status": "healthy", "service": "gamgee-ai-factory"}

    @app.post("/v1/factory/ingest", dependencies=auth)
    async def ingest(request: IngestRequest, tenant: str = Depends(tenant_header)) -> dict:
        try:
            count = await kb.ingest(tenant, request.source, request.text)
        except (VectorStoreError, LLMError) as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"status": "success", "source": request.source, "chunks": count}

    @app.get("/v1/factory/tools", dependencies=auth)
    async def list_tools() -> dict:
        tools = {}
        for server in mcp.servers:
            try:
                tools[server] = await mcp.list_tools(server)
            except MCPError as exc:
                tools[server] = {"error": str(exc)}
        return {"servers": tools}

    @app.post("/v1/factory/chat", dependencies=auth)
    async def chat(request: ChatRequest, tenant: str = Depends(tenant_header)) -> StreamingResponse:
        if request.mode == "tool" and request.tool is None:
            raise HTTPException(422, "mode 'tool' requires a tool call.")
        if request.tool is not None and request.tool.server not in settings.mcp_servers:
            raise HTTPException(422, f"MCP server '{request.tool.server}' is not configured.")
        thread_id = request.thread_id or uuid.uuid4().hex

        async def stream():
            state = {
                "prompt": request.prompt,
                "tenant": tenant,
                "mode": request.mode,
                "tool": request.tool.model_dump() if request.tool else None,
                "event_subject": request.event_subject,
                "context": "",
                "sources": [],
                "notes": [],
            }
            yield sse("thread", {"thread_id": thread_id})
            try:
                async for node, delta in run_graph(app.state.graph, state, thread_id):
                    state.update({k: v for k, v in delta.items() if k != "notes"})
                    yield sse("step", {"node": node, "notes": delta.get("notes", [])})
                if state.get("sources"):
                    yield sse("sources", {"sources": state["sources"]})
                async for token in llm.stream_chat(build_messages(state)):
                    yield sse("token", {"text": token})
                yield sse("done", {})
            except LLMError as exc:
                yield sse("error", {"message": str(exc)})
            except Exception:
                yield sse("error", {"message": "The request could not be completed."})

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return app


def app_factory() -> FastAPI:
    return create_app()
