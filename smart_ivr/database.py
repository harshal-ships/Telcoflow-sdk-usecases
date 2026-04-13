"""
Persistent storage for the Smart IVR agent.
Manages customer accounts, orders, complaints, scheduled callbacks, and call logs.
Seeded with demo data on first run.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Customer:
    id: str
    name: str
    phone: str
    email: str
    plan: str
    balance: float
    created_at: str


@dataclass
class Order:
    id: str
    customer_id: str
    description: str
    status: str
    estimated_delivery: str
    created_at: str


@dataclass
class Complaint:
    id: str
    customer_id: str
    category: str
    description: str
    status: str
    created_at: str


@dataclass
class Callback:
    id: str
    customer_id: str
    phone: str
    preferred_time: str
    reason: str
    status: str
    created_at: str


@dataclass
class CallLog:
    id: str
    caller_number: str
    intent: str
    outcome: str
    duration_seconds: float
    created_at: str


# ---------------------------------------------------------------------------
# Schema + seed data
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    phone      TEXT UNIQUE NOT NULL,
    email      TEXT DEFAULT '',
    plan       TEXT DEFAULT 'basic',
    balance    REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id                 TEXT PRIMARY KEY,
    customer_id        TEXT NOT NULL,
    description        TEXT NOT NULL,
    status             TEXT DEFAULT 'processing',
    estimated_delivery TEXT DEFAULT '',
    created_at         TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS complaints (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    status      TEXT DEFAULT 'open',
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS callbacks (
    id             TEXT PRIMARY KEY,
    customer_id    TEXT DEFAULT '',
    phone          TEXT NOT NULL,
    preferred_time TEXT NOT NULL,
    reason         TEXT DEFAULT '',
    status         TEXT DEFAULT 'scheduled',
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS call_logs (
    id               TEXT PRIMARY KEY,
    caller_number    TEXT NOT NULL,
    intent           TEXT NOT NULL,
    outcome          TEXT NOT NULL,
    duration_seconds REAL DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_complaints_customer ON complaints(customer_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_created ON call_logs(created_at);
"""

_SEED = """
INSERT OR IGNORE INTO customers (id, name, phone, email, plan, balance) VALUES
    ('c-001', 'Alice Nguyen',  '+6591234567', 'alice@example.com',  'premium', 142.50),
    ('c-002', 'Bob Tan',       '+6598765432', 'bob@example.com',    'basic',    23.80),
    ('c-003', 'Carol Lee',     '+6587654321', 'carol@example.com',  'business', 890.00);

INSERT OR IGNORE INTO orders (id, customer_id, description, status, estimated_delivery) VALUES
    ('ord-1001', 'c-001', 'Wireless Router Pro X',  'shipped',     '{delivery_1}'),
    ('ord-1002', 'c-001', 'USB-C Hub 7-in-1',       'processing',  '{delivery_2}'),
    ('ord-1003', 'c-002', 'Noise Cancelling Buds',  'delivered',   '{delivery_3}'),
    ('ord-1004', 'c-003', 'Office Desk Standing',   'shipped',     '{delivery_4}');
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class IVRDB:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)

        async with self._conn.execute("SELECT count(*) as cnt FROM customers") as cur:
            row = await cur.fetchone()
            if row["cnt"] == 0:
                now = datetime.utcnow()
                seed = _SEED.format(
                    delivery_1=(now + timedelta(days=2)).strftime("%Y-%m-%d"),
                    delivery_2=(now + timedelta(days=5)).strftime("%Y-%m-%d"),
                    delivery_3=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
                    delivery_4=(now + timedelta(days=3)).strftime("%Y-%m-%d"),
                )
                await self._conn.executescript(seed)

        await self._conn.commit()
        logger.info("IVRDB ready at %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # -- Customers -----------------------------------------------------------

    async def get_customer_by_phone(self, phone: str) -> Customer | None:
        phone = phone.strip().replace(" ", "")
        async with self._conn.execute(
            "SELECT * FROM customers WHERE phone = ?", (phone,)
        ) as cur:
            row = await cur.fetchone()
        return Customer(**dict(row)) if row else None

    # -- Orders --------------------------------------------------------------

    async def get_orders(self, customer_id: str) -> list[Order]:
        async with self._conn.execute(
            "SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC", (customer_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [Order(**dict(r)) for r in rows]

    async def get_order_by_id(self, order_id: str) -> Order | None:
        async with self._conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ) as cur:
            row = await cur.fetchone()
        return Order(**dict(row)) if row else None

    # -- Complaints ----------------------------------------------------------

    async def log_complaint(
        self, customer_id: str, category: str, description: str
    ) -> Complaint:
        cid = f"cmp-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            "INSERT INTO complaints (id, customer_id, category, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, customer_id, category, description, now),
        )
        await self._conn.commit()
        logger.info("Complaint logged: %s (%s)", cid, category)
        return Complaint(
            id=cid, customer_id=customer_id, category=category,
            description=description, status="open", created_at=now,
        )

    # -- Callbacks -----------------------------------------------------------

    async def schedule_callback(
        self, phone: str, preferred_time: str, reason: str, customer_id: str = ""
    ) -> Callback:
        cbid = f"cb-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            "INSERT INTO callbacks (id, customer_id, phone, preferred_time, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cbid, customer_id, phone, preferred_time, reason, now),
        )
        await self._conn.commit()
        logger.info("Callback scheduled: %s at %s", cbid, preferred_time)
        return Callback(
            id=cbid, customer_id=customer_id, phone=phone,
            preferred_time=preferred_time, reason=reason,
            status="scheduled", created_at=now,
        )

    # -- Call logs -----------------------------------------------------------

    async def log_call(
        self, caller_number: str, intent: str, outcome: str, duration_seconds: float = 0
    ) -> CallLog:
        lid = f"log-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            "INSERT INTO call_logs (id, caller_number, intent, outcome, duration_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lid, caller_number, intent, outcome, duration_seconds, now),
        )
        await self._conn.commit()
        return CallLog(
            id=lid, caller_number=caller_number, intent=intent,
            outcome=outcome, duration_seconds=duration_seconds, created_at=now,
        )
