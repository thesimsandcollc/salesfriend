"""Try the service-reminder conversation end-to-end with no Twilio account,
no phone, no ngrok -- just a terminal.

Usage:
    python -m scripts.simulate_customer_conversation
    python -m scripts.simulate_customer_conversation --phone "+15559998888" --name "Jamie Chen"

Creates (or reuses) a test customer whose last service date is set far
enough in the past to be immediately "due," sends the initial outreach text
the way the automatic loop would, then lets you type replies as that
customer. Type 'quit' to stop. Shows up on /customers once the server is
running.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from app.agent.customer_conversation import generate_customer_reply
from app.compliance import is_opt_out
from app.config import settings
from app.outreach import build_initial_message
from app.storage.db import (
    add_customer_message,
    get_customer_by_phone,
    get_customer_message_history,
    init_db,
    now_iso,
    create_customer,
    update_customer_fields,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", default="+15559998888")
    parser.add_argument("--name", default="Jamie Chen")
    args = parser.parse_args()

    init_db()

    customer = get_customer_by_phone(args.phone)
    if customer is None:
        overdue_date = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()
        customer = create_customer(
            args.phone,
            args.name,
            vehicle_make="Honda",
            vehicle_model="Accord",
            vehicle_year="2020",
            last_service_date=overdue_date,
            last_service_type="Oil change",
        )

    mode = "MOCK (no API key / MOCK_AGENT=true)" if (settings.mock_agent or not settings.anthropic_api_key) else f"LIVE ({settings.anthropic_model})"
    print(f"--- SalesFriend customer-outreach simulator [{mode}] ---")
    print(f"Customer: {customer.name} ({customer.phone})  (view at /customers/{customer.id} once the server is running)")

    greeting = build_initial_message(customer)
    add_customer_message(customer.id, "out", greeting)
    update_customer_fields(customer.phone, status="contacted", last_contacted_at=now_iso())
    print(f"\nSalesFriend: {greeting}")

    while True:
        try:
            text = input("\nYou (the customer): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() == "quit":
            break

        if is_opt_out(text):
            update_customer_fields(customer.phone, status="opted_out", last_contacted_at=now_iso())
            print("(opt-out keyword detected -- status set to opted_out, conversation ends)")
            break

        add_customer_message(customer.id, "in", text)
        history = get_customer_message_history(customer.id)
        reply = generate_customer_reply(customer, history)
        add_customer_message(customer.id, "out", reply.reply_message)
        customer = update_customer_fields(
            customer.phone,
            appointment_time=reply.appointment_time,
            status=reply.status,
            notes=reply.notes,
            last_contacted_at=now_iso(),
        )

        print(f"SalesFriend: {reply.reply_message}")
        print(f"  [status={customer.status} appointment={customer.appointment_time!r}]")

        if reply.status in ("appointment_set", "needs_human"):
            print(f"\n(status is now '{reply.status}' -- in the real app this is when {settings.salesperson_name} gets notified)")


if __name__ == "__main__":
    main()
