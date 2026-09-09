"""The whole "database" for this prototype: a single SQLite file.

No ORM on purpose -- there are exactly two tables and the goal is that
anyone can open salesfriend.db with any SQLite tool and understand it
immediately. This is the "simple standalone list" from the plan doc, not a
calendar sync -- appointment_time is just a text field the AI fills in from
what the lead says (e.g. "Sat 9/6, 10:00 AM"), for a human to read and act on.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    phone               TEXT UNIQUE NOT NULL,
    name                TEXT,
    vehicle_interest    TEXT,
    trade_in            TEXT,
    appointment_time    TEXT,
    status              TEXT NOT NULL DEFAULT 'new',
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_lead_id ON messages(lead_id);

CREATE TABLE IF NOT EXISTS customers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    phone               TEXT UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    vehicle_make        TEXT,
    vehicle_model       TEXT,
    vehicle_year        TEXT,
    last_service_date   TEXT,   -- plain date, e.g. "2026-03-15" -- entered by hand
    last_service_type   TEXT,   -- e.g. "Oil change"
    appointment_time    TEXT,
    status              TEXT NOT NULL DEFAULT 'not_contacted',
    notes               TEXT,
    last_contacted_at   TEXT,   -- set whenever we send THEM a message (outreach or reply)
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    direction    TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customer_messages_customer_id ON customer_messages(customer_id);
"""

# Valid lead.status values, in the order the dashboard should weight them.
# 'opted_out' covers a lead who texts STOP mid-conversation -- rare, but
# handled the same way as the customer pipeline below.
STATUSES = ("new", "texted", "appointment_set", "needs_human", "opted_out")

# Valid customer.status values for the proactive service-reminder pipeline.
CUSTOMER_STATUSES = (
    "not_contacted",
    "contacted",
    "responded",
    "appointment_set",
    "not_interested",
    "needs_human",
    "opted_out",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    """Public alias -- other modules (the outreach loop, main.py) need this
    same timestamp format when they update last_contacted_at themselves."""
    return _now()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    # If DATABASE_PATH points at a mounted persistent disk (e.g. Render's
    # /var/data), the directory exists already -- but this makes the app
    # equally happy with a fresh local subdirectory in dev.
    parent = os.path.dirname(settings.database_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)


@dataclass
class Lead:
    id: int
    phone: str
    name: Optional[str]
    vehicle_interest: Optional[str]
    trade_in: Optional[str]
    appointment_time: Optional[str]
    status: str
    notes: Optional[str]
    created_at: str
    updated_at: str
    messages: list = field(default_factory=list)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Lead":
        return cls(
            id=row["id"],
            phone=row["phone"],
            name=row["name"],
            vehicle_interest=row["vehicle_interest"],
            trade_in=row["trade_in"],
            appointment_time=row["appointment_time"],
            status=row["status"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def get_or_create_lead(phone: str) -> Lead:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
        if row is None:
            now = _now()
            conn.execute(
                "INSERT INTO leads (phone, status, created_at, updated_at) "
                "VALUES (?, 'new', ?, ?)",
                (phone, now, now),
            )
            row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
        return Lead.from_row(row)


def update_lead_fields(
    phone: str,
    *,
    name: Optional[str] = None,
    vehicle_interest: Optional[str] = None,
    trade_in: Optional[str] = None,
    appointment_time: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
) -> Lead:
    """Only overwrites fields that were actually supplied (not None) --
    the agent re-sends everything it currently knows each turn, but callers
    that only learned one new thing can pass just that field."""
    updates: dict[str, str] = {}
    for key, value in (
        ("name", name),
        ("vehicle_interest", vehicle_interest),
        ("trade_in", trade_in),
        ("appointment_time", appointment_time),
        ("status", status),
        ("notes", notes),
    ):
        if value is not None and value != "":
            updates[key] = value

    with get_connection() as conn:
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            params = list(updates.values()) + [_now(), phone]
            conn.execute(
                f"UPDATE leads SET {set_clause}, updated_at = ? WHERE phone = ?",
                params,
            )
        row = conn.execute("SELECT * FROM leads WHERE phone = ?", (phone,)).fetchone()
        return Lead.from_row(row)


def add_message(lead_id: int, direction: str, body: str) -> None:
    assert direction in ("in", "out")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (lead_id, direction, body, created_at) VALUES (?, ?, ?, ?)",
            (lead_id, direction, body, _now()),
        )


def get_message_history(lead_id: int) -> list[dict]:
    """Returns messages oldest-first, shaped for the Anthropic API:
    [{"role": "user"|"assistant", "content": "..."}]"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT direction, body FROM messages WHERE lead_id = ? ORDER BY id ASC",
            (lead_id,),
        ).fetchall()
    return [
        {"role": "user" if r["direction"] == "in" else "assistant", "content": r["body"]}
        for r in rows
    ]


def list_leads() -> list[Lead]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY updated_at DESC").fetchall()
    return [Lead.from_row(r) for r in rows]


def get_lead_by_id(lead_id: int) -> Optional[Lead]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            return None
        lead = Lead.from_row(row)
        msg_rows = conn.execute(
            "SELECT direction, body, created_at FROM messages WHERE lead_id = ? ORDER BY id ASC",
            (lead_id,),
        ).fetchall()
        lead.messages = [dict(m) for m in msg_rows]
        return lead


# ---------------------------------------------------------------------------
# Customers -- the proactive service-reminder pipeline. A separate pair of
# tables from leads/messages on purpose: a "lead" is someone new who called
# about buying a car; a "customer" here is someone already in the shop's
# books being reminded about routine service. Keeping them apart means nothing
# about the (already-tested) missed-call flow above had to change to add this.
# ---------------------------------------------------------------------------


@dataclass
class Customer:
    id: int
    phone: str
    name: str
    vehicle_make: Optional[str]
    vehicle_model: Optional[str]
    vehicle_year: Optional[str]
    last_service_date: Optional[str]
    last_service_type: Optional[str]
    appointment_time: Optional[str]
    status: str
    notes: Optional[str]
    last_contacted_at: Optional[str]
    created_at: str
    updated_at: str
    messages: list = field(default_factory=list)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Customer":
        return cls(
            id=row["id"],
            phone=row["phone"],
            name=row["name"],
            vehicle_make=row["vehicle_make"],
            vehicle_model=row["vehicle_model"],
            vehicle_year=row["vehicle_year"],
            last_service_date=row["last_service_date"],
            last_service_type=row["last_service_type"],
            appointment_time=row["appointment_time"],
            status=row["status"],
            notes=row["notes"],
            last_contacted_at=row["last_contacted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class DuplicateCustomerError(Exception):
    """Raised when creating a customer whose phone number is already on file."""


def create_customer(
    phone: str,
    name: str,
    *,
    vehicle_make: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_year: Optional[str] = None,
    last_service_date: Optional[str] = None,
    last_service_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> Customer:
    now = _now()
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
        if existing is not None:
            raise DuplicateCustomerError(f"{phone} is already in the customer list")
        conn.execute(
            "INSERT INTO customers "
            "(phone, name, vehicle_make, vehicle_model, vehicle_year, last_service_date, "
            " last_service_type, status, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'not_contacted', ?, ?, ?)",
            (phone, name, vehicle_make, vehicle_model, vehicle_year, last_service_date,
             last_service_type, notes, now, now),
        )
        row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
        return Customer.from_row(row)


def get_customer_by_phone(phone: str) -> Optional[Customer]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
        return Customer.from_row(row) if row else None


def update_customer_fields(
    phone: str,
    *,
    name: Optional[str] = None,
    vehicle_make: Optional[str] = None,
    vehicle_model: Optional[str] = None,
    vehicle_year: Optional[str] = None,
    last_service_date: Optional[str] = None,
    last_service_type: Optional[str] = None,
    appointment_time: Optional[str] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    last_contacted_at: Optional[str] = None,
) -> Customer:
    """Same only-overwrite-what's-supplied behavior as update_lead_fields."""
    updates: dict[str, str] = {}
    for key, value in (
        ("name", name),
        ("vehicle_make", vehicle_make),
        ("vehicle_model", vehicle_model),
        ("vehicle_year", vehicle_year),
        ("last_service_date", last_service_date),
        ("last_service_type", last_service_type),
        ("appointment_time", appointment_time),
        ("status", status),
        ("notes", notes),
        ("last_contacted_at", last_contacted_at),
    ):
        if value is not None and value != "":
            updates[key] = value

    with get_connection() as conn:
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            params = list(updates.values()) + [_now(), phone]
            conn.execute(
                f"UPDATE customers SET {set_clause}, updated_at = ? WHERE phone = ?",
                params,
            )
        row = conn.execute("SELECT * FROM customers WHERE phone = ?", (phone,)).fetchone()
        return Customer.from_row(row)


def add_customer_message(customer_id: int, direction: str, body: str) -> None:
    assert direction in ("in", "out")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO customer_messages (customer_id, direction, body, created_at) VALUES (?, ?, ?, ?)",
            (customer_id, direction, body, _now()),
        )


def get_customer_message_history(customer_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT direction, body FROM customer_messages WHERE customer_id = ? ORDER BY id ASC",
            (customer_id,),
        ).fetchall()
    return [
        {"role": "user" if r["direction"] == "in" else "assistant", "content": r["body"]}
        for r in rows
    ]


def list_customers() -> list[Customer]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM customers ORDER BY updated_at DESC").fetchall()
    return [Customer.from_row(r) for r in rows]


def get_customer_by_id(customer_id: int) -> Optional[Customer]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            return None
        customer = Customer.from_row(row)
        msg_rows = conn.execute(
            "SELECT direction, body, created_at FROM customer_messages WHERE customer_id = ? ORDER BY id ASC",
            (customer_id,),
        ).fetchall()
        customer.messages = [dict(m) for m in msg_rows]
        return customer
