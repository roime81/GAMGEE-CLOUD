import itertools
import json

import httpx

from factory.config import Settings

_PROTOCOL_VERSION = "2025-03-26"
_ids = itertools.count(1)


class MCPError(RuntimeError):
    pass


class MCPGateway:
    """Minimal MCP client over Streamable HTTP; only servers in the allowlist are reachable."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._servers = settings.mcp_servers
        self._http = httpx.AsyncClient(timeout=settings.request_timeout, transport=transport)

    @property
    def servers(self) -> list[str]:
        return sorted(self._servers)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_tools(self, server: str) -> list[dict]:
        result = await self._session(server, "tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, server: str, tool: str, arguments: dict) -> str:
        result = await self._session(server, "tools/call", {"name": tool, "arguments": arguments})
        text = "\n".join(
            part.get("text", "") for part in result.get("content", []) if part.get("type") == "text"
        )
        if result.get("isError"):
            raise MCPError(f"Tool '{tool}' reported an error: {text[:500]}")
        return text

    async def _session(self, server: str, method: str, params: dict) -> dict:
        url = self._servers.get(server)
        if url is None:
            raise MCPError(f"MCP server '{server}' is not configured.")
        headers = {"Accept": "application/json, text/event-stream"}
        try:
            init = await self._post(
                url,
                headers,
                {
                    "jsonrpc": "2.0",
                    "id": next(_ids),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "gamgee-ai-factory", "version": "1.0.0"},
                    },
                },
            )
            session_id = init.headers.get("mcp-session-id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            await self._post(
                url, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
            response = await self._post(
                url, headers, {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}
            )
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP server '{server}' is unavailable.") from exc
        message = _decode(response)
        if "error" in message:
            raise MCPError(f"MCP server '{server}' rejected the request.")
        return message.get("result", {})

    async def _post(self, url: str, headers: dict, payload: dict) -> httpx.Response:
        response = await self._http.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response


def _decode(response: httpx.Response) -> dict:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise MCPError("MCP server returned an empty event stream.")
    try:
        return response.json()
    except ValueError as exc:
        raise MCPError("MCP server returned an invalid response.") from exc
