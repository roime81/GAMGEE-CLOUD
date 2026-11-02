import json
from collections.abc import AsyncIterator

import httpx

from factory.config import Settings


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Client for any self-hosted OpenAI-compatible server (vLLM, Ollama, llama.cpp)."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        headers = {}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.llm_base_url,
            headers=headers,
            timeout=settings.request_timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self._http.post(
                "/embeddings",
                json={"model": self._settings.embedding_model, "input": texts},
            )
            response.raise_for_status()
            items = sorted(response.json()["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in items]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise LLMError("The embedding model is unavailable.") from exc

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        payload = {"model": self._settings.llm_model, "messages": messages, "stream": True}
        try:
            async with self._http.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    choices = json.loads(data).get("choices") or []
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if delta:
                        yield delta
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError("The language model is unavailable.") from exc
