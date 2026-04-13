"""
Async SQLite database for the AI receptionist.

Tables:
  customers — known callers (phone is unique lookup key)
  tickets   — open/closed support tickets per customer
  leads     — new callers captured for follow-up
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
    company: str
    created_at: str


@dataclass
class Ticket:
    id: int
    customer_id: int
    subject: str
    status: str
    created_at: str


@dataclass
class Lead:
    id: int
    phone: str
    source: str
    created_at: str


# ---------------------------------------------------------------------------
# Schema + seed data
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT    NOT NULL UNIQUE,
    email       TEXT    NOT NULL DEFAULT '',
    company     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    subject     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'inbound_call',
    created_at  TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_customers_phone  ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets(customer_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status   ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone      ON leads(phone);
"""

_SEED = """
INSERT OR IGNORE INTO customers (id, name, phone, email, company) VALUES
    (1, 'Alice Nguyen',  '+14155550101', 'alice@acmecorp.com',  'Acme Corp'),
    (2, 'Bob Martinez',  '+14155550102', 'bob@globex.io',       'Globex Inc'),
    (3, 'Carol Zhang',   '+14155550103', 'carol@initech.co',    'Initech');

INSERT OR IGNORE INTO tickets (customer_id, subject, status) VALUES
    (1, 'Login page returns 500 after password reset',   'open'),
    (1, 'Cannot export CSV from dashboard',              'open'),
    (2, 'Billing discrepancy on March invoice',          'open'),
    (3, 'Feature request: dark mode',                    'closed');
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class ReceptionistDB:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.executescript(_SEED)
        await self._conn.commit()
        logger.info("ReceptionistDB ready at %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    # -- Lookups ------------------------------------------------------------

    async def get_customer_by_phone(self, phone: str) -> Customer | None:
        async with self._conn.execute(
            "SELECT * FROM customers WHERE phone = ?", (phone,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return Customer(**dict(row))

    async def get_open_tickets(self, customer_id: int) -> list[Ticket]:
        async with self._conn.execute(
            "SELECT * FROM tickets WHERE customer_id = ? AND status = 'open' "
            "ORDER BY created_at DESC",
            (customer_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [Ticket(**dict(r)) for r in rows]

    async def create_lead(self, phone: str, source: str = "inbound_call") -> Lead:
        now = datetime.utcnow().isoformat()
        async with self._conn.execute(
            "INSERT INTO leads (phone, source, created_at) VALUES (?, ?, ?)",
            (phone, source, now),
        ) as cur:
            lead_id = cur.lastrowid
        await self._conn.commit()
        logger.info("New lead %d created for %s", lead_id, phone)
        return Lead(id=lead_id, phone=phone, source=source, created_at=now)
