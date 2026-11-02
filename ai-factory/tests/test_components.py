import json
import unittest

import httpx

from factory.config import Settings
from factory.llm import LLMClient
from factory.mcp import MCPError, MCPGateway
from factory.rag import Chunk, KnowledgeBase, chunk_text, rerank


def settings(**overrides) -> Settings:
    base = dict(
        api_token="secret",
        llm_base_url="http://llm/v1",
        llm_model="m",
        llm_api_key=None,
        embedding_model="e",
        vector_store_url="http://qdrant",
        nats_url=None,
        postgres_checkpoint_url=None,
        mcp_servers={},
        request_timeout=5,
    )
    base.update(overrides)
    return Settings(**base)


class ChunkingTests(unittest.TestCase):
    def test_overlapping_chunks_cover_text(self):
        text = " ".join(f"word{i}" for i in range(400))
        chunks = chunk_text(text, size=200, overlap=40)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 200 for c in chunks))
        self.assertIn("word399", chunks[-1])

    def test_empty_text(self):
        self.assertEqual(chunk_text("   "), [])

    def test_invalid_overlap(self):
        with self.assertRaises(ValueError):
            chunk_text("abc", size=10, overlap=10)


class RerankTests(unittest.TestCase):
    def test_keyword_match_promotes_candidate(self):
        candidates = [
            Chunk("generic text about cats", "a", 0.55),
            Chunk("invoice 4471 payment terms", "b", 0.50),
        ]
        ranked = rerank("invoice 4471", candidates, 2)
        self.assertEqual(ranked[0].source, "b")

    def test_limit_and_empty(self):
        self.assertEqual(rerank("q", [], 3), [])
        self.assertEqual(len(rerank("q", [Chunk("x", "a", 1), Chunk("y", "b", 1)], 1)), 1)


class KnowledgeBaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_creates_collection_and_upserts(self):
        calls = []

        def qdrant(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(404)
            return httpx.Response(200, json={"result": True})

        def llm_handler(request: httpx.Request) -> httpx.Response:
            n = len(json.loads(request.content)["input"])
            return httpx.Response(
                200, json={"data": [{"index": i, "embedding": [0.1, 0.2]} for i in range(n)]}
            )

        cfg = settings()
        llm = LLMClient(cfg, transport=httpx.MockTransport(llm_handler))
        kb = KnowledgeBase(cfg, llm, transport=httpx.MockTransport(qdrant))
        count = await kb.ingest("acme", "doc.md", "hello world")
        self.assertEqual(count, 1)
        self.assertIn(("PUT", "/collections/gamgee_knowledge/points"), calls)

    async def test_search_filters_by_tenant(self):
        seen = {}

        def qdrant(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"result": [{"score": 0.9, "payload": {"text": "t", "source": "s"}}]},
            )

        cfg = settings()
        llm = LLMClient(
            cfg,
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
            ),
        )
        kb = KnowledgeBase(cfg, llm, transport=httpx.MockTransport(qdrant))
        hits = await kb.search("acme", "question")
        self.assertEqual(hits[0].source, "s")
        self.assertEqual(seen["filter"]["must"][0]["match"]["value"], "acme")


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_server_rejected(self):
        gateway = MCPGateway(settings())
        with self.assertRaises(MCPError):
            await gateway.call_tool("nope", "t", {})

    async def test_call_tool_handshake(self):
        methods = []

        def server(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            methods.append(body["method"])
            if body["method"] == "initialize":
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                    headers={"mcp-session-id": "s1"},
                )
            if body["method"] == "notifications/initialized":
                self.assertEqual(request.headers["mcp-session-id"], "s1")
                return httpx.Response(202)
            return httpx.Response(
                200,
                text='event: message\ndata: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":"42"}]}}\n\n',
                headers={"content-type": "text/event-stream"},
            )

        gateway = MCPGateway(
            settings(mcp_servers={"db": "http://mcp/mcp"}), transport=httpx.MockTransport(server)
        )
        self.assertEqual(await gateway.call_tool("db", "count", {}), "42")
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/call"])


if __name__ == "__main__":
    unittest.main()
