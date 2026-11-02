import json
import time
import unittest
from types import SimpleNamespace

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from factory.auth import Authenticator
from factory.events import EventBus
from factory.llm import LLMClient
from factory.main import create_app
from factory.mcp import MCPGateway
from factory.planner import discover_tools, parse_choice
from factory.rag import KnowledgeBase
from tests.test_components import settings

ISSUER, AUDIENCE = "https://idp.example", "gamgee"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


def token(key=KEY, **claims):
    payload = {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300, "tenant": "acme"}
    payload.update(claims)
    return jwt.encode(payload, key, algorithm="RS256")


def mcp_server(tools):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if method == "tools/list":
            result = {"tools": tools}
        else:
            result = {"content": [{"type": "text", "text": f"ran {body['params']['name']}"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    return handler


TOOLS = [
    {"name": "count_users", "description": "Count users", "annotations": {"readOnlyHint": True}},
    {"name": "drop_table", "description": "Danger", "annotations": {"readOnlyHint": False}},
    {"name": "undeclared", "description": "No annotations"},
]


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_read_only_tools_are_discovered(self):
        gateway = MCPGateway(
            settings(mcp_servers={"db": "http://mcp/mcp"}),
            transport=httpx.MockTransport(mcp_server(TOOLS)),
        )
        tools = await discover_tools(gateway)
        self.assertEqual([t["name"] for t in tools], ["count_users"])

    def test_parse_choice_validates_against_catalog(self):
        tools = [{"server": "db", "name": "count_users"}]
        ok = parse_choice('Sure: {"server":"db","name":"count_users","arguments":{"a":1}}', tools)
        self.assertEqual(ok, {"server": "db", "name": "count_users", "arguments": {"a": 1}})
        self.assertIsNone(parse_choice('{"server":"db","name":"drop_table"}', tools))
        self.assertIsNone(parse_choice('{"server":"evil","name":"count_users"}', tools))
        self.assertIsNone(parse_choice('{"tool": null}', tools))
        self.assertIsNone(parse_choice("no json", tools))
        self.assertIsNone(parse_choice('{"server":"db","name":"count_users","arguments":[1]}', tools))

    def test_auto_chat_uses_model_selected_tool(self):
        cfg = settings(mcp_servers={"db": "http://mcp/mcp"})

        def llm_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if request.url.path.endswith("/embeddings"):
                return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
            if not body.get("stream"):
                choice = {"server": "db", "name": "count_users", "arguments": {}}
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(choice)}}]})
            sse = 'data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, text=sse)

        llm = LLMClient(cfg, transport=httpx.MockTransport(llm_handler))
        app = create_app(
            cfg,
            llm=llm,
            kb=KnowledgeBase(cfg, llm, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"result": []}))),
            mcp=MCPGateway(cfg, transport=httpx.MockTransport(mcp_server(TOOLS))),
            bus=EventBus(cfg),
        )
        with TestClient(app) as client:
            text = client.post(
                "/v1/factory/chat",
                json={"prompt": "how many users?"},
                headers={"Authorization": "Bearer secret"},
            ).text
        self.assertIn("Model selected tool db/count_users", text)
        self.assertIn("mcp:db/count_users", text)
        self.assertIn("event: done", text)


class OIDCTests(unittest.TestCase):
    def auth(self):
        cfg = settings(oidc_issuer=ISSUER, oidc_audience=AUDIENCE)
        return Authenticator(cfg, jwks_client=FakeJWKS())

    def test_valid_token_sets_tenant_from_claim_and_ignores_header(self):
        self.assertEqual(self.auth().authenticate(token(), "attacker"), "acme")

    def test_rejects_bad_tokens(self):
        from factory.auth import AuthError

        auth = self.auth()
        cases = {
            "expired": token(exp=int(time.time()) - 10),
            "wrong audience": token(aud="other"),
            "wrong issuer": token(iss="https://evil"),
            "bad signature": token(key=OTHER_KEY),
        }
        for name, bad in cases.items():
            with self.subTest(name), self.assertRaises(AuthError) as ctx:
                auth.authenticate(bad, "default")
            self.assertEqual(ctx.exception.status, 401)

    def test_missing_or_invalid_tenant_claim(self):
        from factory.auth import AuthError

        for claims in ({"tenant": None}, {"tenant": "Bad_Name"}):
            with self.subTest(claims), self.assertRaises(AuthError) as ctx:
                self.auth().authenticate(token(**claims), "default")
            self.assertEqual(ctx.exception.status, 403)

    def test_static_token_still_works_and_unconfigured_fails_closed(self):
        from factory.auth import AuthError

        self.assertEqual(self.auth().authenticate("secret", "beta"), "beta")
        with self.assertRaises(AuthError) as ctx:
            Authenticator(settings(api_token=None)).authenticate("secret", "beta")
        self.assertEqual(ctx.exception.status, 503)

    def test_http_with_jwt(self):
        cfg = settings(oidc_issuer=ISSUER, oidc_audience=AUDIENCE)
        llm = LLMClient(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})))
        kb = KnowledgeBase(cfg, llm, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"result": True})))
        app = create_app(cfg, llm=llm, kb=kb, mcp=MCPGateway(cfg), bus=EventBus(cfg), authenticator=self.auth())
        with TestClient(app) as client:
            ok = client.post("/v1/factory/ingest", json={"source": "a", "text": "hi"}, headers={"Authorization": f"Bearer {token()}"})
            bad = client.post("/v1/factory/ingest", json={"source": "a", "text": "hi"}, headers={"Authorization": "Bearer a.b.c"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(bad.status_code, 401)


if __name__ == "__main__":
    unittest.main()
