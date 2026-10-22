from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from ..config import Settings

Row = dict[str, Any]


def J(value: Any) -> Jsonb:
    """Wrap a Python value for a jsonb parameter."""
    return Jsonb(value)


class Database:
    def __init__(self, settings: Settings, *, single: bool = False, open_pool: bool = True, pool_size: int = 12) -> None:
        self._dsn = settings.dsn
        self._connection: ContextVar[psycopg.Connection[Row] | None] = ContextVar("transaction_connection", default=None)
        self._after_commit: ContextVar[list[Callable[[], None]] | None] = ContextVar("after_commit", default=None)
        self._single = psycopg.connect(settings.dsn, row_factory=dict_row, autocommit=True) if single else None
        self.pool = (
            None
            if single
            else ConnectionPool(
                settings.dsn, min_size=1, max_size=pool_size, kwargs={"row_factory": dict_row, "autocommit": True}, open=open_pool
            )
        )

    @contextmanager
    def conn(self) -> Iterator[psycopg.Connection[Row]]:
        current = self._connection.get()
        if current is not None:
            yield current
            return
        if self._single is not None:
            yield self._single
            return
        assert self.pool is not None
        with self.pool.connection() as c:
            yield c  # type: ignore[misc]

    @contextmanager
    def independent_conn(self) -> Iterator[psycopg.Connection[Row]]:
        """Short autocommit IO, independent of job locks and the main pool's capacity."""
        with psycopg.connect(self._dsn, row_factory=dict_row, autocommit=True, connect_timeout=5) as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.conn() as c:
            token = self._connection.set(c)
            pending = self._after_commit.get()
            outer = pending is None
            callbacks: list[Callable[[], None]] = [] if pending is None else pending
            callback_token = self._after_commit.set(callbacks)
            start = len(callbacks)
            try:
                with c.transaction():
                    yield
            except BaseException:
                del callbacks[start:]
                raise
            finally:
                self._connection.reset(token)
                self._after_commit.reset(callback_token)
        if outer:
            for callback in callbacks:
                callback()

    def defer(self, callback: Callable[[], None]) -> bool:
        pending = self._after_commit.get()
        if pending is None:
            return False
        pending.append(callback)
        return True

    @contextmanager
    def session_lock(self, key: str, namespace: int) -> Iterator[None]:
        """Serialize IO without a transaction; reuse the held connection for nested queries.

        Advisory locks belong to a database session and are re-entrant inside it, so this serializes callers only when
        they hold different connections: the pool gives each thread its own. A `single=True` Database shares one
        session between all threads and therefore serializes nothing between them."""
        with self.conn() as connection:
            token = self._connection.set(connection)
            try:
                connection.execute("SELECT pg_advisory_lock(hashtextextended(%s,%s))", (key, namespace))
                try:
                    yield
                finally:
                    connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,%s))", (key, namespace))
            finally:
                self._connection.reset(token)

    @contextmanager
    def try_session_lock(self, key: str, namespace: int) -> Iterator[bool]:
        with self.conn() as connection:
            row = connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,%s)) AS acquired", (key, namespace)
            ).fetchone()
            acquired = bool(row and row["acquired"])
            token = self._connection.set(connection)
            try:
                yield acquired
            finally:
                try:
                    if acquired:
                        connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s,%s))", (key, namespace))
                finally:
                    self._connection.reset(token)

    def all(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Row]:
        with self.conn() as c:
            return c.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Row | None:
        with self.conn() as c:
            return c.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> int:
        with self.conn() as c:
            return c.execute(sql, params).rowcount

    def close(self) -> None:
        if self._single is not None:
            self._single.close()
        if self.pool is not None:
            self.pool.close()
