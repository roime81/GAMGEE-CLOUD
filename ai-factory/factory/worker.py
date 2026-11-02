"""Integration worker: turns bus events into AI Factory runs.

Consumes `gamgee.<tenant>.<subject>` messages from NATS JetStream and replays each as a
chat request against the orchestrator, so file watchers, DB triggers and legacy adapters
only need to publish an event.
"""

import asyncio
import json
import logging
import os

import httpx
import nats
from nats.js.api import StreamConfig

log = logging.getLogger("gamgee.worker")


async def handle(http: httpx.AsyncClient, subject: str, data: bytes) -> None:
    tenant = subject.split(".")[1]
    prompt = json.loads(data)["prompt"]
    async with http.stream(
        "POST",
        "/v1/factory/chat",
        headers={"X-Gamgee-Tenant": tenant},
        json={"prompt": prompt, "mode": "knowledge"},
    ) as response:
        response.raise_for_status()
        async for _ in response.aiter_lines():
            pass
    log.info("processed event for tenant %s", tenant)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.environ["GAMGEE_API_TOKEN"]
    nc = await nats.connect(os.environ["NATS_URL"])
    js = nc.jetstream()
    await js.add_stream(StreamConfig(name="GAMGEE", subjects=["gamgee.>"]))
    sub = await js.pull_subscribe("gamgee.>", durable="integration-worker")
    async with httpx.AsyncClient(
        base_url=os.environ["ORCHESTRATOR_URL"],
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    ) as http:
        while True:
            try:
                messages = await sub.fetch(10, timeout=5)
            except nats.errors.TimeoutError:
                continue
            for msg in messages:
                try:
                    await handle(http, msg.subject, msg.data)
                    await msg.ack()
                except (httpx.HTTPError, KeyError, ValueError, IndexError):
                    log.exception("failed to process %s", msg.subject)
                    await msg.nak(delay=30)


if __name__ == "__main__":
    asyncio.run(main())
