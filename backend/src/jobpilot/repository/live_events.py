"""One dedicated PG listener per API process; SSE libraries own framing and disconnects."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Annotated

import psycopg
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sse_starlette import ServerSentEvent

log = logging.getLogger(__name__)
CHANNEL = "jobpilot_events"
QUEUE_SIZE = 64
MAX_CLIENTS = 128


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    job: Annotated[str, Field(pattern=r"^j_[a-z2-7]{8}$")] | None
    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")]
    id: Annotated[int, Field(gt=0)]


@dataclass(eq=False)
class Subscription:
    queue: asyncio.Queue[ServerSentEvent | None] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))


class LiveEvents:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._clients: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._pool: AsyncConnectionPool[psycopg.AsyncConnection[tuple[object, ...]]] | None = None
        self.connected = False

    async def start(self) -> None:
        # The existing psycopg pool owns connection replacement and exponential reconnect backoff.
        # No browser ever borrows this connection; the sole consumer keeps it checked out.
        self._pool = AsyncConnectionPool(
            self._dsn,
            min_size=1,
            max_size=1,
            num_workers=1,
            max_waiting=1,
            kwargs={
                "autocommit": True,
                "connect_timeout": 5,
                "options": "-c statement_timeout=5000",
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
            configure=self._configure,
            open=False,
            name="dashboard-listener",
        )
        await self._pool.open()
        self._task = asyncio.create_task(self._listen(), name="dashboard-listener")

    @staticmethod
    async def _configure(connection: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        await connection.execute("LISTEN jobpilot_events")

    async def stop(self) -> None:
        try:
            if self._task is not None:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
        finally:
            self._task = None
            self._disconnect()
            if self._pool is not None:
                await self._pool.close()
                self._pool = None

    async def _listen(self) -> None:
        assert self._pool is not None
        try:
            while True:
                try:
                    async with self._pool.connection() as connection:
                        try:
                            self.connected = True
                            await self.consume(connection.notifies())
                        finally:
                            # Discard a failed listener; pool replacement reruns LISTEN before use.
                            await connection.close()
                except (psycopg.Error, PoolTimeout):
                    log.warning("Dashboard listener unavailable; safety polling remains active")
                finally:
                    self._disconnect()
        finally:
            self._disconnect()

    async def consume(self, notifications: AsyncIterator[psycopg.Notify]) -> None:
        """Consume the driver's iterator (also the seam for offline fan-out tests)."""
        async for notice in notifications:
            if notice.channel != CHANNEL or len(notice.payload) > 1024:
                continue
            try:
                hint = Notification.model_validate_json(notice.payload)
            except ValidationError:
                log.warning("Discarded invalid dashboard notification")  # never log the payload
                continue
            event = ServerSentEvent(
                event="job" if hint.job is not None else "automation",
                data=json.dumps({"job": hint.job, "kind": hint.kind} if hint.job is not None else {}),
                id=str(hint.id),
            )
            for client in tuple(self._clients):
                if client.queue.full():
                    self.unsubscribe(client)
                else:
                    client.queue.put_nowait(event)

    def subscribe(self) -> Subscription:
        if not self.connected:
            raise ConnectionError("dashboard listener unavailable")
        if len(self._clients) >= MAX_CLIENTS:
            raise OverflowError("too many dashboard streams")
        client = Subscription()
        self._clients.add(client)
        return client

    def unsubscribe(self, client: Subscription) -> None:
        self._clients.discard(client)
        while not client.queue.empty():
            client.queue.get_nowait()
        client.queue.put_nowait(None)

    def _disconnect(self) -> None:
        self.connected = False
        for client in tuple(self._clients):
            self.unsubscribe(client)

    async def stream(self, client: Subscription) -> AsyncIterator[ServerSentEvent]:
        try:
            yield ServerSentEvent(comment="connected")
            while (event := await client.queue.get()) is not None:
                yield event
        finally:
            self.unsubscribe(client)
