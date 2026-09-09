"""Try the AI conversation end-to-end with no Twilio account, no phone, no
ngrok -- just a terminal. This is the fastest way to see whether the agent's
questions and appointment-setting logic actually feel right before wiring up
real phones.

Usage:
    python -m scripts.simulate_conversation
    python -m scripts.simulate_conversation --phone "+15551234567"

Every message you type plays the role of the lead replying by text. Type
'quit' to stop. Leads created this way show up on the normal /dashboard.
"""
from __future__ import annotations

import argparse

from app.agent.conversation import generate_reply
from app.config import settings
from app.storage.db import add_message, get_message_history, get_or_create_lead, init_db, update_lead_fields


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phone", default="+15550001111", help="Fake phone number to use as the lead's identity")
    args = parser.parse_args()

    init_db()
    lead = get_or_create_lead(args.phone)

    mode = "MOCK (no API key / MOCK_AGENT=true)" if (settings.mock_agent or not settings.anthropic_api_key) else f"LIVE ({settings.anthropic_model})"
    print(f"--- SalesFriend conversation simulator [{mode}] ---")
    print(f"Lead: {args.phone}  (view at /dashboard/{lead.id} once the server is running)")

    greeting = (
        f"Hi! This is {settings.dealership_name} -- sorry we missed your call! "
        f"I'm {settings.salesperson_name}'s assistant. What can I help you find today?"
    )
    add_message(lead.id, "out", greeting)
    update_lead_fields(args.phone, status="texted")
    print(f"\nSalesFriend: {greeting}")

    while True:
        try:
            text = input("\nYou (the lead): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() == "quit":
            break

        add_message(lead.id, "in", text)
        history = get_message_history(lead.id)
        reply = generate_reply(lead, history)
        add_message(lead.id, "out", reply.reply_message)
        lead = update_lead_fields(
            args.phone,
            vehicle_interest=reply.vehicle_interest,
            trade_in=reply.trade_in,
            appointment_time=reply.appointment_time,
            status=reply.status,
            notes=reply.notes,
        )

        print(f"SalesFriend: {reply.reply_message}")
        print(f"  [status={lead.status} vehicle={lead.vehicle_interest!r} trade_in={lead.trade_in!r} appointment={lead.appointment_time!r}]")

        if reply.status in ("appointment_set", "needs_human"):
            print(f"\n(status is now '{reply.status}' -- in the real app this is when {settings.salesperson_name} gets notified)")


if __name__ == "__main__":
    main()
