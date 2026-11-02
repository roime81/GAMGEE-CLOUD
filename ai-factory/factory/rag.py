import math
import re
import uuid
from dataclasses import dataclass

import httpx

from factory.config import Settings
from factory.llm import LLMClient

_NAMESPACE = uuid.UUID("6f1c2f5e-3c55-4b43-9b1d-0c1f6f1b7a11")
_TOKEN = re.compile(r"\w+")


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str
    score: float = 0.0


class VectorStoreError(RuntimeError):
    pass


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    if size <= overlap:
        raise ValueError("size must be greater than overlap")
    text = text.strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer breaking on whitespace so words are not split.
            boundary = text.rfind(" ", start + size // 2, end)
            end = boundary if boundary != -1 else end
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def rerank(query: str, candidates: list[Chunk], limit: int) -> list[Chunk]:
    """Blend dense similarity with BM25 keyword scoring over the candidate set."""
    if not candidates:
        return []
    terms = _TOKEN.findall(query.lower())
    docs = [_TOKEN.findall(c.text.lower()) for c in candidates]
    avg_len = sum(len(d) for d in docs) / len(docs) or 1.0
    n = len(docs)
    bm25 = []
    for doc in docs:
        score = 0.0
        for term in set(terms):
            tf = doc.count(term)
            if not tf:
                continue
            df = sum(1 for d in docs if term in d)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(doc) / avg_len))
        bm25.append(score)
    top = max(bm25) or 1.0
    scored = [
        Chunk(c.text, c.source, 0.6 * c.score + 0.4 * (b / top))
        for c, b in zip(candidates, bm25)
    ]
    return sorted(scored, key=lambda c: c.score, reverse=True)[:limit]


class KnowledgeBase:
    """Per-tenant document store on Qdrant: tenant is a payload filter on one collection."""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        transport: httpx.AsyncBaseTransport | None = None,
        collection: str = "gamgee_knowledge",
    ):
        self._llm = llm
        self._collection = collection
        self._ready = False
        self._http = httpx.AsyncClient(
            base_url=settings.vector_store_url,
            timeout=settings.request_timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_collection(self, dim: int) -> None:
        if self._ready:
            return
        response = await self._http.get(f"/collections/{self._collection}")
        if response.status_code == 404:
            created = await self._http.put(
                f"/collections/{self._collection}",
                json={"vectors": {"size": dim, "distance": "Cosine"}},
            )
            created.raise_for_status()
            index = await self._http.put(
                f"/collections/{self._collection}/index",
                json={"field_name": "tenant", "field_schema": "keyword"},
            )
            index.raise_for_status()
        else:
            response.raise_for_status()
        self._ready = True

    async def ingest(self, tenant: str, source: str, text: str) -> int:
        chunks = chunk_text(text)
        if not chunks:
            return 0
        try:
            vectors = await self._llm.embed(chunks)
            await self._ensure_collection(len(vectors[0]))
            points = [
                {
                    # Deterministic ids make re-ingesting the same source idempotent.
                    "id": str(uuid.uuid5(_NAMESPACE, f"{tenant}/{source}/{i}")),
                    "vector": vector,
                    "payload": {"tenant": tenant, "source": source, "text": chunk},
                }
                for i, (chunk, vector) in enumerate(zip(chunks, vectors))
            ]
            response = await self._http.put(
                f"/collections/{self._collection}/points",
                params={"wait": "true"},
                json={"points": points},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VectorStoreError("The vector store is unavailable.") from exc
        return len(chunks)

    async def search(self, tenant: str, query: str, limit: int = 4) -> list[Chunk]:
        try:
            [vector] = await self._llm.embed([query])
            response = await self._http.post(
                f"/collections/{self._collection}/points/search",
                json={
                    "vector": vector,
                    "limit": limit * 4,
                    "with_payload": True,
                    "filter": {"must": [{"key": "tenant", "match": {"value": tenant}}]},
                },
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            hits = response.json()["result"]
        except httpx.HTTPError as exc:
            raise VectorStoreError("The vector store is unavailable.") from exc
        candidates = [
            Chunk(h["payload"]["text"], h["payload"]["source"], float(h["score"])) for h in hits
        ]
        return rerank(query, candidates, limit)
