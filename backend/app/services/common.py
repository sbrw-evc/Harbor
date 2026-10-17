from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def current(value):
    """A setting handed to a service either as a value or as a function that returns the current value (the settings
    change while the panel runs, app/runtime.py)."""
    return value() if callable(value) else value


@dataclass(frozen=True)
class Actor:
    username: str
    role: str
    ip: str = ""

    @classmethod
    def system(cls) -> "Actor":
        return cls(username="system", role="admin")


class EventBus:
    def __init__(self):
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: str, data: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:
                pass
