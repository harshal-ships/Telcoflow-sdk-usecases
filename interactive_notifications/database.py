"""
Notification database using aiosqlite.

Tables:
  - customers: caller identity (phone is unique, used for lookup)
  - notifications: per-customer messages with status lifecycle

Status lifecycle:  pending → acknowledged | follow_up
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Customer:
    id: int
    name: str
    phone: str
    email: str
    created_at: str


@dataclass
class Notification:
    id: int
    customer_id: int
    message: str
    status: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Schema + seed data
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT    NOT NULL UNIQUE,
    email       TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    message     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'acknowledged', 'follow_up')),
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notifications_customer
    ON notifications(customer_id);
CREATE INDEX IF NOT EXISTS idx_notifications_status
    ON notifications(status);
"""

_SEED_CUSTOMERS = [
    ("Alice Nguyen", "+14155550101", "alice.nguyen@example.com"),
    ("Bob Martinez", "+14155550102", "bob.martinez@example.com"),
    ("Carol Zhang", "+14155550103", "carol.zhang@example.com"),
]

_SEED_NOTIFICATIONS = [
    # Alice — 3 pending
    (1, "Your annual account review is due by April 30. Please schedule a meeting with your advisor."),
    (1, "Payment of $245.00 for invoice #INV-2024-0389 is overdue. Please remit at your earliest convenience."),
    (1, "Your subscription plan will auto-renew on May 1. Contact us to make changes."),
    # Bob — 2 pending
    (2, "A technician visit has been scheduled for April 15 between 10 AM and 12 PM. Please confirm availability."),
    (2, "Your support ticket #TK-78214 has been updated. Our team needs additional information from you."),
    # Carol — 3 pending
    (3, "Your order #ORD-55123 has shipped and is expected to arrive by April 12."),
    (3, "We noticed unusual login activity on your account from a new device. Please verify this was you."),
    (3, "You have an upcoming appointment on April 14 at 3 PM. Reply to confirm or reschedule."),
]


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class NotificationDB:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        await self._seed_if_empty()
        logger.info("NotificationDB ready at %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # -- Seed ----------------------------------------------------------------

    async def _seed_if_empty(self) -> None:
        async with self._conn.execute("SELECT COUNT(*) FROM customers") as cur:
            row = await cur.fetchone()
        if row[0] > 0:
            return

        logger.info("Seeding database with sample customers and notifications …")
        for name, phone, email in _SEED_CUSTOMERS:
            await self._conn.execute(
                "INSERT INTO customers (name, phone, email) VALUES (?, ?, ?)",
                (name, phone, email),
            )
        for customer_id, message in _SEED_NOTIFICATIONS:
            await self._conn.execute(
                "INSERT INTO notifications (customer_id, message) VALUES (?, ?)",
                (customer_id, message),
            )
        await self._conn.commit()
        logger.info(
            "Seeded %d customers, %d notifications",
            len(_SEED_CUSTOMERS),
            len(_SEED_NOTIFICATIONS),
        )

    # -- Queries -------------------------------------------------------------

    async def get_customer_by_phone(self, phone: str) -> Customer | None:
        async with self._conn.execute(
            "SELECT * FROM customers WHERE phone = ?", (phone,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return Customer(**dict(row))

    async def get_pending_notifications(self, customer_id: int) -> list[Notification]:
        async with self._conn.execute(
            "SELECT * FROM notifications WHERE customer_id = ? AND status = 'pending' "
            "ORDER BY created_at ASC",
            (customer_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [Notification(**dict(r)) for r in rows]

    async def mark_acknowledged(self, notification_id: int) -> bool:
        now = datetime.utcnow().isoformat()
        cur = await self._conn.execute(
            "UPDATE notifications SET status = 'acknowledged', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, notification_id),
        )
        await self._conn.commit()
        changed = cur.rowcount > 0
        if changed:
            logger.info("Notification %d acknowledged", notification_id)
        return changed

    async def flag_for_followup(self, notification_id: int) -> bool:
        now = datetime.utcnow().isoformat()
        cur = await self._conn.execute(
            "UPDATE notifications SET status = 'follow_up', updated_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, notification_id),
        )
        await self._conn.commit()
        changed = cur.rowcount > 0
        if changed:
            logger.info("Notification %d flagged for follow-up", notification_id)
        return changed
