import json
import re

from factory.config import Settings

_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,64}(\.[A-Za-z0-9_-]{1,64}){0,3}$")


class EventBusError(RuntimeError):
    pass


class EventBus:
    """Publishes work items to NATS JetStream for the integration worker to pick up."""

    def __init__(self, settings: Settings):
        self._url = settings.nats_url
        self._nc = None

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    async def publish(self, tenant: str, subject: str, payload: dict) -> None:
        if not self._url:
            raise EventBusError("The event bus is not configured.")
        if not _SUBJECT.fullmatch(subject):
            raise EventBusError("Invalid event subject.")
        try:
            import nats

            if self._nc is None or self._nc.is_closed:
                self._nc = await nats.connect(self._url, connect_timeout=5)
            js = self._nc.jetstream()
            await js.publish(
                f"gamgee.{tenant}.{subject}",
                json.dumps(payload).encode(),
            )
        except Exception as exc:
            raise EventBusError("The event bus is unavailable.") from exc

    async def aclose(self) -> None:
        if self._nc is not None and not self._nc.is_closed:
            await self._nc.close()
