"""Durable submission ledger and local process ownership (POSIX)."""

import fcntl
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO

from .models import OrderIntent


@dataclass(frozen=True)
class JournalOrder:
    client_order_id: str
    intent: OrderIntent
    margin: float
    state: str


class OrderJournal:
    def __init__(self, path: str | Path, identity: str) -> None:
        self.path = Path(path).expanduser().resolve()
        if not identity:
            raise ValueError("account identity is required")
        self.identity = identity
        self._lock: IO[bytes] | None = None
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Journal is not open")
        return self._connection

    def open(self) -> None:
        if self._lock is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = open(str(self.path) + ".lock", "a+b")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock.close()
            raise RuntimeError(f"Runtime already running at {self.path}") from error
        self._lock = lock
        try:
            self._connection = sqlite3.connect(self.path)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            with self.connection:
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL)"
                )
                row = self.connection.execute("SELECT value FROM identity").fetchone()
                if row is None:
                    self.connection.execute(
                        "INSERT INTO identity VALUES (?)", (self.identity,)
                    )
                elif row[0] != self.identity:
                    raise ValueError("Journal account identity mismatch")
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, intent TEXT NOT NULL, margin REAL NOT NULL, state TEXT NOT NULL)"
                )
        except BaseException:
            self.close()
            raise

    def prepare(self, intent: OrderIntent, margin: float) -> str:
        identifier = "obf-" + uuid.uuid4().hex
        data = asdict(intent)
        data["side"] = intent.side.value
        data["order_type"] = intent.order_type.value
        with self.connection:
            self.connection.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?)",
                (identifier, json.dumps(data, allow_nan=False), margin, "prepared"),
            )
        return identifier

    def update(self, identifier: str, state: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE orders SET state=? WHERE id=?", (state, identifier)
            )

    def pending(self) -> list[JournalOrder]:
        return [
            JournalOrder(identifier, OrderIntent(**json.loads(intent)), margin, state)
            for identifier, intent, margin, state in self.connection.execute(
                "SELECT id,intent,margin,state FROM orders WHERE state IN ('prepared','unknown','accepted','cancel_unknown')"
            )
        ]

    def close(self) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            if self._lock is not None:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
                self._lock = None
