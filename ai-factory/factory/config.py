import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_token: str | None
    llm_base_url: str
    llm_model: str
    llm_api_key: str | None
    embedding_model: str
    vector_store_url: str
    nats_url: str | None
    postgres_checkpoint_url: str | None
    mcp_servers: dict[str, str]
    request_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_token=os.getenv("GAMGEE_API_TOKEN"),
            llm_base_url=os.getenv("LLM_BASE_URL", "http://ollama:11434/v1").rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "llama3.1:8b"),
            llm_api_key=os.getenv("LLM_API_KEY"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "nomic-embed-text"),
            vector_store_url=os.getenv("VECTOR_STORE_URL", "http://qdrant:6333").rstrip("/"),
            nats_url=os.getenv("NATS_URL"),
            postgres_checkpoint_url=os.getenv("POSTGRES_CHECKPOINT_URL"),
            mcp_servers=_parse_mcp_servers(os.getenv("MCP_SERVERS", "")),
            request_timeout=float(os.getenv("FACTORY_REQUEST_TIMEOUT", "120")),
        )


def _parse_mcp_servers(raw: str) -> dict[str, str]:
    """MCP_SERVERS is a JSON object of {name: streamable-http URL}; it is the allowlist."""
    if not raw.strip():
        return {}
    servers = json.loads(raw)
    if not isinstance(servers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v.startswith(("http://", "https://"))
        for k, v in servers.items()
    ):
        raise ValueError("MCP_SERVERS must be a JSON object mapping names to http(s) URLs.")
    return servers
