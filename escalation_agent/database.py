"""
Persistent call context storage using aiosqlite.
Tracks whether each call was handled by AI or escalated to a human agent.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CallContext:
    id: str
    call_id: str
    caller_number: str
    summary_json: str
    escalation_reason: str
    status: str  # "ai_handled" | "escalated"
    created_at: str

    @property
    def summary(self) -> dict:
        try:
            return json.loads(self.summary_json) if self.summary_json else {}
        except json.JSONDecodeError:
            return {}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS call_contexts (
    id                TEXT PRIMARY KEY,
    call_id           TEXT NOT NULL,
    caller_number     TEXT NOT NULL,
    summary_json      TEXT DEFAULT '{}',
    escalation_reason TEXT DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'ai_handled',
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_call_contexts_call_id
    ON call_contexts(call_id);
CREATE INDEX IF NOT EXISTS idx_call_contexts_status
    ON call_contexts(status);
CREATE INDEX IF NOT EXISTS idx_call_contexts_created
    ON call_contexts(created_at);
"""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class EscalationDB:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("EscalationDB ready at %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    async def save_call_context(
        self,
        call_id: str,
        caller_number: str,
        *,
        summary: dict | str = "",
        escalation_reason: str = "",
        status: str = "ai_handled",
    ) -> CallContext:
        ctx_id = f"ctx-{uuid.uuid4().hex[:8]}"
        created = datetime.utcnow().isoformat()

        if isinstance(summary, dict):
            summary_json = json.dumps(summary)
        else:
            summary_json = summary or "{}"

        await self._conn.execute(
            "INSERT INTO call_contexts "
            "(id, call_id, caller_number, summary_json, escalation_reason, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ctx_id, call_id, caller_number, summary_json, escalation_reason, status, created),
        )
        await self._conn.commit()

        logger.info(
            "Saved call context %s (call=%s, status=%s)", ctx_id, call_id, status,
        )
        return CallContext(
            id=ctx_id,
            call_id=call_id,
            caller_number=caller_number,
            summary_json=summary_json,
            escalation_reason=escalation_reason,
            status=status,
            created_at=created,
        )

    async def get_call_context(self, call_id: str) -> CallContext | None:
        async with self._conn.execute(
            "SELECT * FROM call_contexts WHERE call_id = ? ORDER BY created_at DESC LIMIT 1",
            (call_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return CallContext(**dict(row))
        return None
