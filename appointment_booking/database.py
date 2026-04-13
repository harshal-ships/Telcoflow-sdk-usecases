"""
Persistent storage for customers and appointments using aiosqlite.
Schema is auto-created on first run.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

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
    created_at: str


@dataclass
class Appointment:
    id: str
    customer_id: str
    service: str
    date: str
    time: str
    status: str
    created_at: str


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    phone      TEXT UNIQUE NOT NULL,
    email      TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS appointments (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    service     TEXT NOT NULL,
    date        TEXT NOT NULL,
    time        TEXT NOT NULL,
    status      TEXT DEFAULT 'confirmed',
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE INDEX IF NOT EXISTS idx_appointments_customer
    ON appointments(customer_id);
CREATE INDEX IF NOT EXISTS idx_appointments_date_status
    ON appointments(date, status);
CREATE INDEX IF NOT EXISTS idx_customers_phone
    ON customers(phone);
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """Async SQLite wrapper for customer and appointment data."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("Database ready at %s", self._db_path)

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
        if not row:
            return None
        return Customer(**dict(row))

    async def create_customer(
        self, name: str, phone: str, email: str = ""
    ) -> Customer:
        cid = f"cust-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            "INSERT INTO customers (id, name, phone, email, created_at) VALUES (?, ?, ?, ?, ?)",
            (cid, name, phone, email, now),
        )
        await self._conn.commit()
        logger.info("Created customer %s (%s)", name, cid)
        return Customer(id=cid, name=name, phone=phone, email=email, created_at=now)

    async def get_or_create_customer(
        self, phone: str, name: str = "Unknown"
    ) -> Customer:
        existing = await self.get_customer_by_phone(phone)
        if existing:
            return existing
        return await self.create_customer(name=name, phone=phone)

    # -- Appointments --------------------------------------------------------

    async def get_upcoming_appointments(self, customer_id: str) -> list[Appointment]:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        async with self._conn.execute(
            "SELECT * FROM appointments WHERE customer_id = ? AND date >= ? AND status = 'confirmed' ORDER BY date, time",
            (customer_id, today),
        ) as cur:
            rows = await cur.fetchall()
        return [Appointment(**dict(r)) for r in rows]

    async def get_booked_times(self, date: str) -> set[str]:
        async with self._conn.execute(
            "SELECT time FROM appointments WHERE date = ? AND status = 'confirmed'",
            (date,),
        ) as cur:
            rows = await cur.fetchall()
        return {row["time"] for row in rows}

    async def create_appointment(
        self,
        customer_id: str,
        service: str,
        date: str,
        time_slot: str,
    ) -> Appointment:
        aid = f"appt-{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow().isoformat()
        await self._conn.execute(
            "INSERT INTO appointments (id, customer_id, service, date, time, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'confirmed', ?)",
            (aid, customer_id, service, date, time_slot, now),
        )
        await self._conn.commit()
        logger.info("Booked %s for customer %s on %s at %s", aid, customer_id, date, time_slot)
        return Appointment(
            id=aid,
            customer_id=customer_id,
            service=service,
            date=date,
            time=time_slot,
            status="confirmed",
            created_at=now,
        )

    async def cancel_appointment(self, appointment_id: str) -> bool:
        cur = await self._conn.execute(
            "UPDATE appointments SET status = 'cancelled' WHERE id = ? AND status = 'confirmed'",
            (appointment_id,),
        )
        await self._conn.commit()
        return cur.rowcount > 0
