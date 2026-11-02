import json
import unittest

import httpx
from fastapi.testclient import TestClient

from factory.llm import LLMClient
from factory.main import create_app
from factory.mcp import MCPGateway
from factory.rag import KnowledgeBase
from factory.events import EventBus
from factory.orchestrator import route_request
from tests.test_components import settings


def llm_server(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/embeddings"):
        n = len(json.loads(request.content)["input"])
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0]} for i in range(n)]})
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def qdrant_server(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/points/search"):
        return httpx.Response(
            200, json={"result": [{"score": 0.9, "payload": {"text": "Gamgee is tiny", "source": "wiki"}}]}
        )
    return httpx.Response(200, json={"result": True})


def make_client(**overrides) -> TestClient:
    cfg = settings(mcp_servers={"db": "http://mcp/mcp"}, **overrides)
    llm = LLMClient(cfg, transport=httpx.MockTransport(llm_server))
    app = create_app(
        cfg,
        llm=llm,
        kb=KnowledgeBase(cfg, llm, transport=httpx.MockTransport(qdrant_server)),
        mcp=MCPGateway(cfg),
        bus=EventBus(cfg),
    )
    return TestClient(app)


AUTH = {"Authorization": "Bearer secret"}


class ChatApiTests(unittest.TestCase):
    def test_requires_token(self):
        with make_client() as client:
            self.assertEqual(client.post("/v1/factory/chat", json={"prompt": "hi"}).status_code, 401)
            bad = client.post("/v1/factory/chat", json={"prompt": "hi"}, headers={"Authorization": "Bearer x"})
            self.assertEqual(bad.status_code, 401)

    def test_unconfigured_token_fails_closed(self):
        with make_client(api_token=None) as client:
            self.assertEqual(client.post("/v1/factory/chat", json={"prompt": "hi"}, headers=AUTH).status_code, 503)

    def test_streams_steps_sources_and_tokens(self):
        with make_client() as client:
            response = client.post("/v1/factory/chat", json={"prompt": "What is Gamgee?"}, headers=AUTH)
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("event: step", text)
        self.assertIn('"sources": ["wiki"]', text)
        self.assertLess(text.index("event: step"), text.index("event: token"))
        self.assertIn('{"text": "Hel"}', text)
        self.assertIn("event: done", text)

    def test_rejects_unknown_mcp_server_and_bad_tenant(self):
        with make_client() as client:
            tool = {"prompt": "x", "tool": {"server": "evil", "name": "t"}}
            self.assertEqual(client.post("/v1/factory/chat", json=tool, headers=AUTH).status_code, 422)
            bad = client.post(
                "/v1/factory/chat", json={"prompt": "x"}, headers={**AUTH, "X-Gamgee-Tenant": "Bad_Tenant"}
            )
            self.assertEqual(bad.status_code, 422)

    def test_ingest(self):
        with make_client() as client:
            response = client.post("/v1/factory/ingest", json={"source": "a.md", "text": "hello"}, headers=AUTH)
        self.assertEqual(response.json(), {"status": "success", "source": "a.md", "chunks": 1})

    def test_retrieval_failure_degrades_to_plain_answer(self):
        def vectors_down(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        cfg = settings()
        llm = LLMClient(cfg, transport=httpx.MockTransport(llm_server))
        app = create_app(
            cfg,
            llm=llm,
            kb=KnowledgeBase(cfg, llm, transport=httpx.MockTransport(vectors_down)),
            mcp=MCPGateway(cfg),
            bus=EventBus(cfg),
        )
        with TestClient(app) as client:
            text = client.post("/v1/factory/chat", json={"prompt": "hi"}, headers=AUTH).text
        self.assertIn("Knowledge base skipped", text)
        self.assertIn("event: done", text)

    def test_workspace_served(self):
        with make_client() as client:
            self.assertIn("Gamgee AI Factory", client.get("/").text)


class RoutingTests(unittest.TestCase):
    def test_auto_routing(self):
        self.assertEqual(route_request({"mode": "auto", "tool": {"server": "a"}}), "tool")
        self.assertEqual(route_request({"mode": "auto", "event_subject": "x"}), "event")
        self.assertEqual(route_request({"mode": "auto"}), "knowledge")
        self.assertEqual(route_request({"mode": "chat"}), "chat")


if __name__ == "__main__":
    unittest.main()
