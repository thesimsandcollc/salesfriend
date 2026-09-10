"""Switchboard's storage layer: a single SQLite file, no ORM.

Same reasoning as the SalesFriend app this lives alongside (see
`storage/db.py`): a handful of tables anyone can open with any SQLite tool
and understand immediately, not a schema that needs a migration framework.

Tables:
  api_keys           Bearer tokens that authenticate calls to /v1/*.
  numbers            Phone numbers this account owns (via the carrier).
  messages           Every SMS sent or received through /v1/messages.
  calls              Every voice call initiated through /v1/calls.
  opt_outs           Phone numbers that have texted STOP (or similar).
  webhook_endpoints  URLs registered to receive event notifications.
  webhook_deliveries Delivery attempts for each event, so failures can be
                     retried and inspected instead of silently vanishing.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator, Optional

from switchboard.config import settings
from switchboard.ids import (
    new_api_key,
    new_call_id,
    new_message_id,
    new_signing_secret,
    new_webhook_delivery_id,
    new_webhook_endpoint_id,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash    TEXT UNIQUE NOT NULL,
    label       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at  TEXT
);

CREATE TABLE IF NOT EXISTS numbers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number    TEXT UNIQUE NOT NULL,
    carrier         TEXT NOT NULL,
    sms_capable     INTEGER NOT NULL DEFAULT 1,
    voice_capable   INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'active',
    order_id        TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id          TEXT UNIQUE NOT NULL,
    direction           TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    from_number         TEXT NOT NULL,
    to_number           TEXT NOT NULL,
    body                TEXT NOT NULL,
    status              TEXT NOT NULL,
    carrier_message_id  TEXT,
    error_code          TEXT,
    error_message       TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_carrier_message_id ON messages(carrier_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_to_number ON messages(to_number);

CREATE TABLE IF NOT EXISTS calls (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id             TEXT UNIQUE NOT NULL,
    direction           TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    from_number         TEXT NOT NULL,
    to_number           TEXT NOT NULL,
    status              TEXT NOT NULL,
    say_message         TEXT,
    forward_to          TEXT,
    status_webhook_url  TEXT,
    carrier_call_id     TEXT,
    answered_by         TEXT,
    duration_seconds    INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_carrier_call_id ON calls(carrier_call_id);

CREATE TABLE IF NOT EXISTS opt_outs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number    TEXT UNIQUE NOT NULL,
    opted_out_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id     TEXT UNIQUE NOT NULL,
    url             TEXT NOT NULL,
    signing_secret  TEXT NOT NULL,
    event_types     TEXT NOT NULL DEFAULT 'all',
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id         TEXT UNIQUE NOT NULL,
    endpoint_id         TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    payload             TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed', 'abandoned')),
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     TEXT NOT NULL,
    last_attempt_at     TEXT,
    last_response_code  INTEGER,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status ON webhook_deliveries(status, next_attempt_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_iso() -> str:
    return _now()


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


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
    parent = os.path.dirname(settings.database_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


@dataclass
class ApiKey:
    id: int
    label: str
    created_at: str
    last_used_at: Optional[str]
    revoked_at: Optional[str]


def create_api_key(label: str) -> tuple[ApiKey, str]:
    """Returns the record plus the plaintext key -- the only time the
    plaintext is ever available. Only the SHA-256 hash is stored."""
    plaintext = new_api_key()
    now = _now()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO api_keys (key_hash, label, created_at) VALUES (?, ?, ?)",
            (_hash_key(plaintext), label, now),
        )
        row_id = cur.lastrowid
    return ApiKey(id=row_id, label=label, created_at=now, last_used_at=None, revoked_at=None), plaintext


def verify_api_key(plaintext: str) -> Optional[ApiKey]:
    key_hash = _hash_key(plaintext)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (key_hash,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), row["id"]))
    return ApiKey(
        id=row["id"], label=row["label"], created_at=row["created_at"],
        last_used_at=row["last_used_at"], revoked_at=row["revoked_at"],
    )


def list_api_keys() -> list[ApiKey]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
    return [
        ApiKey(id=r["id"], label=r["label"], created_at=r["created_at"],
               last_used_at=r["last_used_at"], revoked_at=r["revoked_at"])
        for r in rows
    ]


def count_active_api_keys() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM api_keys WHERE revoked_at IS NULL").fetchone()
    return row["n"]


def revoke_api_key(key_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE api_keys SET revoked_at = ? WHERE id = ?", (_now(), key_id))


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


@dataclass
class Number:
    id: int
    phone_number: str
    carrier: str
    sms_capable: bool
    voice_capable: bool
    status: str
    order_id: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Number":
        return cls(
            id=row["id"], phone_number=row["phone_number"], carrier=row["carrier"],
            sms_capable=bool(row["sms_capable"]), voice_capable=bool(row["voice_capable"]),
            status=row["status"], order_id=row["order_id"], created_at=row["created_at"],
        )


def record_number(
    phone_number: str, carrier: str, *, sms_capable: bool = True, voice_capable: bool = True,
    order_id: Optional[str] = None,
) -> Number:
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO numbers (phone_number, carrier, sms_capable, voice_capable, status, order_id, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?) "
            "ON CONFLICT(phone_number) DO UPDATE SET status = 'active'",
            (phone_number, carrier, int(sms_capable), int(voice_capable), order_id, now),
        )
        row = conn.execute("SELECT * FROM numbers WHERE phone_number = ?", (phone_number,)).fetchone()
    return Number.from_row(row)


def list_numbers() -> list[Number]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM numbers ORDER BY created_at DESC").fetchall()
    return [Number.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@dataclass
class Message:
    id: int
    message_id: str
    direction: str
    from_number: str
    to_number: str
    body: str
    status: str
    carrier_message_id: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Message":
        return cls(
            id=row["id"], message_id=row["message_id"], direction=row["direction"],
            from_number=row["from_number"], to_number=row["to_number"], body=row["body"],
            status=row["status"], carrier_message_id=row["carrier_message_id"],
            error_code=row["error_code"], error_message=row["error_message"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )


def create_message(
    *, direction: str, from_number: str, to_number: str, body: str, status: str,
    carrier_message_id: Optional[str] = None,
) -> Message:
    assert direction in ("outbound", "inbound")
    message_id = new_message_id()
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (message_id, direction, from_number, to_number, body, status, "
            " carrier_message_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_id, direction, from_number, to_number, body, status, carrier_message_id, now, now),
        )
        row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    return Message.from_row(row)


def update_message_status(
    message_id: str, status: str, *, carrier_message_id: Optional[str] = None,
    error_code: Optional[str] = None, error_message: Optional[str] = None,
) -> Optional[Message]:
    with get_connection() as conn:
        fields = ["status = ?", "updated_at = ?"]
        params: list = [status, _now()]
        if carrier_message_id is not None:
            fields.append("carrier_message_id = ?")
            params.append(carrier_message_id)
        if error_code is not None:
            fields.append("error_code = ?")
            params.append(error_code)
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(error_message)
        params.append(message_id)
        conn.execute(f"UPDATE messages SET {', '.join(fields)} WHERE message_id = ?", params)
        row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    return Message.from_row(row) if row else None


def get_message_by_carrier_id(carrier_message_id: str) -> Optional[Message]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM messages WHERE carrier_message_id = ?", (carrier_message_id,)
        ).fetchone()
    return Message.from_row(row) if row else None


def get_message(message_id: str) -> Optional[Message]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
    return Message.from_row(row) if row else None


def list_messages(limit: int = 50, *, to_number: Optional[str] = None, from_number: Optional[str] = None) -> list[Message]:
    query = "SELECT * FROM messages"
    clauses, params = [], []
    if to_number:
        clauses.append("to_number = ?")
        params.append(to_number)
    if from_number:
        clauses.append("from_number = ?")
        params.append(from_number)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Message.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------


@dataclass
class Call:
    id: int
    call_id: str
    direction: str
    from_number: str
    to_number: str
    status: str
    say_message: Optional[str]
    forward_to: Optional[str]
    status_webhook_url: Optional[str]
    carrier_call_id: Optional[str]
    answered_by: Optional[str]
    duration_seconds: Optional[int]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Call":
        return cls(
            id=row["id"], call_id=row["call_id"], direction=row["direction"],
            from_number=row["from_number"], to_number=row["to_number"], status=row["status"],
            say_message=row["say_message"], forward_to=row["forward_to"],
            status_webhook_url=row["status_webhook_url"], carrier_call_id=row["carrier_call_id"],
            answered_by=row["answered_by"], duration_seconds=row["duration_seconds"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )


def create_call(
    *, direction: str, from_number: str, to_number: str, status: str,
    say_message: Optional[str] = None, forward_to: Optional[str] = None,
    status_webhook_url: Optional[str] = None, carrier_call_id: Optional[str] = None,
) -> Call:
    call_id = new_call_id()
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO calls (call_id, direction, from_number, to_number, status, say_message, "
            " forward_to, status_webhook_url, carrier_call_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (call_id, direction, from_number, to_number, status, say_message, forward_to,
             status_webhook_url, carrier_call_id, now, now),
        )
        row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    return Call.from_row(row)


def update_call(
    call_id: str, *, status: Optional[str] = None, answered_by: Optional[str] = None,
    duration_seconds: Optional[int] = None, carrier_call_id: Optional[str] = None,
) -> Optional[Call]:
    with get_connection() as conn:
        fields, params = ["updated_at = ?"], [_now()]
        for col, val in (
            ("status", status), ("answered_by", answered_by),
            ("duration_seconds", duration_seconds), ("carrier_call_id", carrier_call_id),
        ):
            if val is not None:
                fields.append(f"{col} = ?")
                params.append(val)
        params.append(call_id)
        conn.execute(f"UPDATE calls SET {', '.join(fields)} WHERE call_id = ?", params)
        row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    return Call.from_row(row) if row else None


def get_call(call_id: str) -> Optional[Call]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    return Call.from_row(row) if row else None


def get_call_by_carrier_id(carrier_call_id: str) -> Optional[Call]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM calls WHERE carrier_call_id = ?", (carrier_call_id,)).fetchone()
    return Call.from_row(row) if row else None


def list_calls(limit: int = 50) -> list[Call]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM calls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [Call.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Opt-outs -- STOP/START compliance, shared by every number on this account.
# ---------------------------------------------------------------------------


def is_opted_out(phone_number: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM opt_outs WHERE phone_number = ?", (phone_number,)).fetchone()
    return row is not None


def add_opt_out(phone_number: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO opt_outs (phone_number, opted_out_at) VALUES (?, ?) "
            "ON CONFLICT(phone_number) DO NOTHING",
            (phone_number, _now()),
        )


def remove_opt_out(phone_number: str) -> None:
    """A number texting START (the reciprocal CTIA keyword) re-opts-in.
    Unlike SalesFriend's per-lead 'opted_out' status (sticky by design for a
    single conversation thread), this list is meant to be exactly what real
    carriers expect apps to honor both ways."""
    with get_connection() as conn:
        conn.execute("DELETE FROM opt_outs WHERE phone_number = ?", (phone_number,))


def list_opt_outs() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT phone_number FROM opt_outs ORDER BY opted_out_at DESC").fetchall()
    return [r["phone_number"] for r in rows]


# ---------------------------------------------------------------------------
# Webhook endpoints + deliveries
# ---------------------------------------------------------------------------


@dataclass
class WebhookEndpoint:
    id: int
    endpoint_id: str
    url: str
    signing_secret: str
    event_types: str
    active: bool
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "WebhookEndpoint":
        return cls(
            id=row["id"], endpoint_id=row["endpoint_id"], url=row["url"],
            signing_secret=row["signing_secret"], event_types=row["event_types"],
            active=bool(row["active"]), created_at=row["created_at"],
        )

    def wants(self, event_type: str) -> bool:
        if self.event_types == "all":
            return True
        return event_type in {e.strip() for e in self.event_types.split(",")}


def create_webhook_endpoint(url: str, event_types: str = "all") -> WebhookEndpoint:
    endpoint_id = new_webhook_endpoint_id()
    secret = new_signing_secret()
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO webhook_endpoints (endpoint_id, url, signing_secret, event_types, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (endpoint_id, url, secret, event_types, now),
        )
        row = conn.execute("SELECT * FROM webhook_endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
    return WebhookEndpoint.from_row(row)


def list_webhook_endpoints(active_only: bool = False) -> list[WebhookEndpoint]:
    query = "SELECT * FROM webhook_endpoints"
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY created_at DESC"
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
    return [WebhookEndpoint.from_row(r) for r in rows]


def deactivate_webhook_endpoint(endpoint_id: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE webhook_endpoints SET active = 0 WHERE endpoint_id = ?", (endpoint_id,))


@dataclass
class WebhookDelivery:
    id: int
    delivery_id: str
    endpoint_id: str
    event_type: str
    payload: str
    status: str
    attempts: int
    next_attempt_at: str
    last_attempt_at: Optional[str]
    last_response_code: Optional[int]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "WebhookDelivery":
        return cls(
            id=row["id"], delivery_id=row["delivery_id"], endpoint_id=row["endpoint_id"],
            event_type=row["event_type"], payload=row["payload"], status=row["status"],
            attempts=row["attempts"], next_attempt_at=row["next_attempt_at"],
            last_attempt_at=row["last_attempt_at"], last_response_code=row["last_response_code"],
            created_at=row["created_at"],
        )


def create_webhook_delivery(endpoint_id: str, event_type: str, payload: str) -> WebhookDelivery:
    delivery_id = new_webhook_delivery_id()
    now = _now()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO webhook_deliveries (delivery_id, endpoint_id, event_type, payload, status, "
            " attempts, next_attempt_at, created_at) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (delivery_id, endpoint_id, event_type, payload, now, now),
        )
        row = conn.execute("SELECT * FROM webhook_deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
    return WebhookDelivery.from_row(row)


def due_webhook_deliveries(limit: int = 50) -> list[WebhookDelivery]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM webhook_deliveries WHERE status IN ('pending', 'failed') "
            "AND next_attempt_at <= ? ORDER BY next_attempt_at ASC LIMIT ?",
            (_now(), limit),
        ).fetchall()
    return [WebhookDelivery.from_row(r) for r in rows]


def record_delivery_attempt(
    delivery_id: str, *, status: str, response_code: Optional[int], next_attempt_at: Optional[str],
) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE webhook_deliveries SET status = ?, attempts = attempts + 1, "
            " last_attempt_at = ?, last_response_code = ?, next_attempt_at = ? WHERE delivery_id = ?",
            (status, _now(), response_code, next_attempt_at or _now(), delivery_id),
        )


def list_recent_deliveries(limit: int = 50) -> list[WebhookDelivery]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM webhook_deliveries ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [WebhookDelivery.from_row(r) for r in rows]
