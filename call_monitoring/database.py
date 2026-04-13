"""
Persistent call analytics storage using aiosqlite.
Records sentiment, topics, and coaching events for monitored calls.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CallAnalytics:
    id: str
    call_id: str
    caller_number: str
    timestamp: str
    sentiment: str
    topics: str
    coaching_given: str
    created_at: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_analytics (
    id              TEXT PRIMARY KEY,
    call_id         TEXT NOT NULL,
    caller_number   TEXT NOT NULL DEFAULT '',
    timestamp       TEXT NOT NULL,
    sentiment       TEXT NOT NULL DEFAULT 'neutral',
    topics          TEXT DEFAULT '',
    coaching_given  TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analytics_call_id
    ON call_analytics(call_id);
CREATE INDEX IF NOT EXISTS idx_analytics_sentiment
    ON call_analytics(sentiment);
CREATE INDEX IF NOT EXISTS idx_analytics_created
    ON call_analytics(created_at);
"""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class AnalyticsDB:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("AnalyticsDB ready at %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def log_analytics(
        self,
        call_id: str,
        sentiment: str,
        topics: str = "",
        caller_number: str = "",
        coaching_given: str = "",
    ) -> CallAnalytics:
        aid = f"ca-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow()
        timestamp = now.isoformat()

        await self._conn.execute(
            "INSERT INTO call_analytics "
            "(id, call_id, caller_number, timestamp, sentiment, topics, coaching_given, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, call_id, caller_number, timestamp, sentiment, topics, coaching_given, timestamp),
        )
        await self._conn.commit()

        logger.info(
            "Logged analytics %s for call %s — sentiment=%s topics=%s",
            aid, call_id, sentiment, topics,
        )
        return CallAnalytics(
            id=aid,
            call_id=call_id,
            caller_number=caller_number,
            timestamp=timestamp,
            sentiment=sentiment,
            topics=topics,
            coaching_given=coaching_given,
            created_at=timestamp,
        )

    async def get_call_analytics(self, call_id: str) -> list[CallAnalytics]:
        async with self._conn.execute(
            "SELECT * FROM call_analytics WHERE call_id = ? ORDER BY created_at ASC",
            (call_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [CallAnalytics(**dict(r)) for r in rows]

    async def get_recent(self, limit: int = 50) -> list[CallAnalytics]:
        async with self._conn.execute(
            "SELECT * FROM call_analytics ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [CallAnalytics(**dict(r)) for r in rows]
