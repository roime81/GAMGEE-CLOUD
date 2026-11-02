import json

from factory.llm import LLMClient, LLMError
from factory.mcp import MCPError, MCPGateway

_PLANNER_PROMPT = (
    "You decide whether a user request needs one of the available tools. "
    'Reply with only JSON: {"tool": null} if no tool is needed, otherwise '
    '{"server": "<server>", "name": "<tool>", "arguments": {...}}. '
    "Use only tools from the list and arguments that match their schema."
)
_MAX_TOOLS = 40


def _readonly(tool: dict) -> bool:
    return (tool.get("annotations") or {}).get("readOnlyHint") is True


async def discover_tools(mcp: MCPGateway) -> list[dict]:
    """Read-only tools across all servers. Tools that do not declare readOnlyHint are never auto-selected."""
    found = []
    for server in mcp.servers:
        try:
            tools = await mcp.list_tools(server)
        except MCPError:
            continue
        found.extend({"server": server, **t} for t in tools if _readonly(t) and t.get("name"))
    return found[:_MAX_TOOLS]


def parse_choice(raw: str, tools: list[dict]) -> dict | None:
    """Validate the model's choice against the discovered tool list; anything else is ignored."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        choice = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    if not isinstance(choice, dict) or not choice.get("name"):
        return None
    arguments = choice.get("arguments", {})
    if not isinstance(arguments, dict):
        return None
    for tool in tools:
        if tool["server"] == choice.get("server") and tool["name"] == choice["name"]:
            return {"server": tool["server"], "name": tool["name"], "arguments": arguments}
    return None


async def plan_tool(llm: LLMClient, mcp: MCPGateway, prompt: str) -> dict | None:
    tools = await discover_tools(mcp)
    if not tools:
        return None
    catalog = [
        {
            "server": t["server"],
            "name": t["name"],
            "description": (t.get("description") or "")[:300],
            "inputSchema": t.get("inputSchema", {}),
        }
        for t in tools
    ]
    try:
        raw = await llm.complete(
            [
                {"role": "system", "content": _PLANNER_PROMPT},
                {"role": "user", "content": f"Tools:\n{json.dumps(catalog)}\n\nRequest: {prompt}"},
            ]
        )
    except LLMError:
        return None
    return parse_choice(raw, tools)
