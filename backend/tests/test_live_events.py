"""Offline notification contract, bounded fan-out and library disconnect coverage."""

import asyncio
import json
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
from sse_starlette import EventSourceResponse

from jobpilot.repository.live_events import CHANNEL, MAX_CLIENTS, QUEUE_SIZE, LiveEvents


async def notices(*values: dict[str, Any]) -> AsyncIterator[psycopg.Notify]:
    for value in values:
        yield psycopg.Notify(CHANNEL, json.dumps(value), 1)


HINT = {"job": "j_abcdefgh", "kind": "run_requeued", "id": 42}


class LiveEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.hub = LiveEvents("unused")
        self.hub.connected = True

    async def test_fanout_and_json_allowlist(self) -> None:
        first, second = self.hub.subscribe(), self.hub.subscribe()
        await self.hub.consume(notices(HINT))
        event = first.queue.get_nowait()
        self.assertIs(event, second.queue.get_nowait())
        assert event is not None
        self.assertEqual(event.event, "job")
        self.assertEqual(event.id, "42")
        self.assertEqual(json.loads(event.data), {"job": HINT["job"], "kind": HINT["kind"]})
        self.assertIn(b"id: 42\r\nevent: job\r\n", event.encode())
        await self.hub.consume(notices({"job": None, "kind": "automation", "id": 43}))
        event = first.queue.get_nowait()
        assert event is not None
        self.assertEqual((event.event, event.data, event.id), ("automation", "{}", "43"))

    async def test_invalid_notifications_are_not_forwarded(self) -> None:
        client = self.hub.subscribe()
        invalid = [
            {**HINT, "payload": {"answer": "private"}},
            {**HINT, "job": "https://example.com"},
            {**HINT, "kind": "bad\nkind"},
            {**HINT, "id": True},
            {**HINT, "id": -1},
            {**HINT, "kind": "x" * 2000},
        ]
        with self.assertLogs("jobpilot.repository.live_events", level="WARNING") as logs:
            await self.hub.consume(notices(*invalid))
        self.assertNotIn("private", " ".join(logs.output))
        self.assertTrue(client.queue.empty())

    async def test_slow_client_is_dropped_without_blocking_other_tabs(self) -> None:
        slow, fast = self.hub.subscribe(), self.hub.subscribe()
        for _ in range(QUEUE_SIZE):
            await self.hub.consume(notices(HINT))
            fast.queue.get_nowait()
        self.assertEqual(slow.queue.qsize(), QUEUE_SIZE)
        await self.hub.consume(notices(HINT))
        self.assertIsNone(slow.queue.get_nowait())
        self.assertEqual(len(self.hub._clients), 1)
        self.assertIsNotNone(fast.queue.get_nowait())

    async def test_stream_close_unregisters_client(self) -> None:
        client = self.hub.subscribe()
        stream = self.hub.stream(client)
        await anext(stream)
        await stream.aclose()
        self.assertEqual(len(self.hub._clients), 0)

    async def test_sse_library_disconnect_cleans_up(self) -> None:
        client = self.hub.subscribe()
        sent = asyncio.Event()

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body":
                sent.set()

        async def receive() -> Any:
            await sent.wait()
            return {"type": "http.disconnect"}

        response = EventSourceResponse(self.hub.stream(client), ping=15, send_timeout=1)
        await asyncio.wait_for(response({"type": "http"}, receive, send), 1)
        self.assertEqual(len(self.hub._clients), 0)

    async def test_pg_disconnect_closes_clients_and_capacity_is_bounded(self) -> None:
        clients = [self.hub.subscribe() for _ in range(MAX_CLIENTS)]
        with self.assertRaises(OverflowError):
            self.hub.subscribe()
        self.hub._disconnect()
        self.assertTrue(all(client.queue.get_nowait() is None for client in clients))
        self.assertEqual(len(self.hub._clients), 0)
        with self.assertRaises(ConnectionError):
            self.hub.subscribe()

    async def test_configure_listens_on_replacement_connection(self) -> None:
        connection = AsyncMock()
        await self.hub._configure(connection)
        connection.execute.assert_awaited_once_with("LISTEN jobpilot_events")

    async def test_listener_failure_evicts_clients_and_uses_pool_replacement(self) -> None:
        first_read, fail, replaced = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def old_notifies() -> AsyncIterator[psycopg.Notify]:
            yield psycopg.Notify(CHANNEL, json.dumps(HINT), 1)
            first_read.set()
            await fail.wait()
            raise psycopg.OperationalError("listener lost")

        async def new_notifies() -> AsyncIterator[psycopg.Notify]:
            replaced.set()
            await asyncio.Event().wait()
            yield psycopg.Notify(CHANNEL, json.dumps(HINT), 1)

        old = MagicMock(notifies=old_notifies, close=AsyncMock())
        new = MagicMock(notifies=new_notifies, close=AsyncMock())
        borrowed = 0

        @asynccontextmanager
        async def borrow() -> AsyncIterator[Any]:
            nonlocal borrowed
            borrowed += 1
            yield old if borrowed == 1 else new

        self.hub._pool = MagicMock(connection=borrow, close=AsyncMock())
        client = self.hub.subscribe()
        self.hub._task = asyncio.create_task(self.hub._listen())
        try:
            await asyncio.wait_for(first_read.wait(), 1)
            self.assertIsNotNone(client.queue.get_nowait())
            with self.assertLogs("jobpilot.repository.live_events", level="WARNING"):
                fail.set()
                await asyncio.wait_for(replaced.wait(), 1)
            self.assertIsNone(client.queue.get_nowait())
            self.assertEqual(len(self.hub._clients), 0)
            self.assertTrue(self.hub.connected)
            old.close.assert_awaited_once()
        finally:
            await self.hub.stop()
        new.close.assert_awaited_once()

    async def test_lifecycle_uses_one_pool_connection_and_closes_it(self) -> None:
        pool = AsyncMock()
        with patch("jobpilot.repository.live_events.AsyncConnectionPool", return_value=pool) as factory:
            await self.hub.start()
            await self.hub.stop()
        self.assertEqual(factory.call_args.kwargs["max_size"], 1)
        pool.open.assert_awaited_once()
        pool.close.assert_awaited_once()
        self.assertFalse(self.hub.connected)


class NotificationMigrationTests(unittest.TestCase):
    def test_trigger_payload_has_only_identifiers_and_kind(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "migrations" / "0030_live_events.sql"
        text = migration.read_text()
        self.assertIn("AFTER INSERT ON job_events", text)
        self.assertIn("FOR EACH ROW EXECUTE FUNCTION notify_jobpilot_event()", text)
        self.assertIn("json_build_object('job', NEW.job_id, 'kind', NEW.kind, 'id', NEW.id)::text", text)
        self.assertNotIn("NEW.payload", text)
        self.assertNotIn("NEW.url", text)
        self.assertIn("ON automation", text)
        self.assertIn("ON runtime_settings", text)
