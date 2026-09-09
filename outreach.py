"""The recurring side of the customer-outreach pipeline: deciding who's due
for a service reminder, and sending the first text.

No external scheduler (cron, APScheduler) -- just an asyncio loop started
once at app startup (see app/main.py) that wakes up periodically and checks
the customer list. That's enough for one dealership's volume and keeps this
prototype to stdlib-only for the scheduling part.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings
from app.storage.db import Customer, add_customer_message, list_customers, now_iso, update_customer_fields

# `app.telephony` (and the Twilio SDK behind it) is imported lazily inside
# run_due_outreach() -- everything else in this module (the due-date math,
# the message text) is pure logic that shouldn't require Twilio just to
# import, e.g. from the simulate_customer_conversation.py script.

log = logging.getLogger("salesfriend.outreach")


def _parse_anchor(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        # last_service_date is a plain "YYYY-MM-DD" entered by hand;
        # created_at/last_contacted_at are already timezone-aware.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def next_due_at(customer: Customer, interval_days: int) -> Optional[datetime]:
    """When this customer next becomes due, or None for an opted-out customer
    (they're never due). Used both by is_due() and by the dashboard, so the
    date shown on screen always matches what actually triggers a text."""
    if customer.status == "opted_out":
        return None
    anchor_str = customer.last_contacted_at or customer.last_service_date or customer.created_at
    if not anchor_str:
        return datetime.now(timezone.utc)
    return _parse_anchor(anchor_str) + timedelta(days=interval_days)


def is_due(customer: Customer, interval_days: int, now: Optional[datetime] = None) -> bool:
    """A customer is due once `interval_days` have passed since whichever is
    more recent: the last time we contacted them, or their last known
    service. Falls back to when they were added if neither is known. Anyone
    opted out is never due, full stop."""
    due_at = next_due_at(customer, interval_days)
    if due_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now >= due_at


def build_initial_message(customer: Customer) -> str:
    parts = [p for p in (customer.vehicle_year, customer.vehicle_make, customer.vehicle_model) if p]
    vehicle = " ".join(parts) if parts else "your vehicle"

    if customer.last_service_type and customer.last_service_date:
        service_note = f" It's been a while since your last {customer.last_service_type.lower()} ({customer.last_service_date})."
    else:
        service_note = " It's been a while since your last visit."

    return (
        f"Hi {customer.name}, this is {settings.dealership_name}'s service team checking in on {vehicle}."
        f"{service_note} Want to schedule an oil change or tire rotation? Reply STOP to opt out anytime."
    )


def run_due_outreach() -> list[str]:
    """Texts everyone currently due. Returns the phone numbers texted, mainly
    so callers (the manual "run now" button, tests) can see what happened."""
    from app.telephony import provider  # deferred -- see note at top of file

    sent: list[str] = []
    for customer in list_customers():
        if not is_due(customer, settings.outreach_interval_days):
            continue
        message = build_initial_message(customer)
        provider.send_sms(to=customer.phone, body=message)
        add_customer_message(customer.id, "out", message)
        new_status = "contacted" if customer.status == "not_contacted" else customer.status
        update_customer_fields(customer.phone, status=new_status, last_contacted_at=now_iso())
        sent.append(customer.phone)
        log.info("Outreach sent to %s", customer.phone)
    return sent


async def outreach_loop() -> None:
    """Runs for the lifetime of the app. A failed check (e.g. Twilio hiccup)
    is logged and retried next tick rather than killing the loop."""
    while True:
        try:
            run_due_outreach()
        except Exception:
            log.exception("Outreach check failed")
        await asyncio.sleep(settings.outreach_check_interval_seconds)
