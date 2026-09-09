"""AI conversation logic for the proactive service-reminder pipeline --
texting existing customers about oil changes, tire rotations, etc.

This mirrors app/agent/conversation.py's shape on purpose but is a separate
file: that sales-lead flow is already tested and working, and duplicating a
little code here keeps that path completely untouched while this new one
gets built out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.storage.db import Customer

# `anthropic` is imported lazily inside generate_customer_reply(), not here --
# see the note in app/agent/conversation.py for why.

TOOL_NAME = "respond_to_customer"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Compose the next SMS reply to an existing customer being reminded "
        "about routine service, and record what this turn revealed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_message": {
                "type": "string",
                "description": (
                    "The SMS to send back. Warm, brief, low-pressure -- under "
                    "~300 characters, no markdown, no bullet points."
                ),
            },
            "appointment_time": {
                "type": "string",
                "description": "The appointment day/time as agreed or currently proposed. Omit if none discussed yet.",
            },
            "status": {
                "type": "string",
                "enum": [
                    "contacted",
                    "responded",
                    "appointment_set",
                    "not_interested",
                    "needs_human",
                    "opted_out",
                ],
                "description": (
                    "'responded' = they replied but nothing decided yet. "
                    "'appointment_set' = a specific time was agreed. "
                    "'not_interested' = they don't want to schedule right now but "
                    "didn't ask to stop hearing from the dealership -- fine to "
                    "reach out again next cycle. 'opted_out' = they asked to stop "
                    "being contacted altogether -- reply_message should confirm "
                    "that, not ask another question. 'needs_human' = a complaint, "
                    "a detailed pricing/financing question, or they ask for a "
                    "specific person by name."
                ),
            },
            "notes": {
                "type": "string",
                "description": "A short internal summary update, one sentence.",
            },
        },
        "required": ["reply_message", "status"],
    },
}


def _vehicle_desc(customer: Customer) -> str:
    parts = [p for p in (customer.vehicle_year, customer.vehicle_make, customer.vehicle_model) if p]
    return " ".join(parts) if parts else "their vehicle"


def _system_prompt(customer: Customer) -> str:
    if customer.last_service_type and customer.last_service_date:
        last_service = f"Their last recorded service was {customer.last_service_type} on {customer.last_service_date}."
    else:
        last_service = "We don't have a recorded last-service date for them."

    return f"""You are texting on behalf of {settings.dealership_name}'s service department with an \
existing customer, {customer.name}, about {_vehicle_desc(customer)}. {last_service}

Your job:
1. Keep it warm and low-pressure -- this is a courtesy reminder, not a sales pitch.
2. If they want to schedule, get a specific day/time and confirm it back to them.
3. If they say not right now / not interested, thank them and back off gracefully -- \
set status to not_interested. Don't push or ask again in this conversation.
4. If they ask to stop being contacted, or say anything like "stop texting me" \
(even outside the literal STOP keyword), set status to opted_out and confirm \
plainly that they won't hear from you again.
5. If something needs a real person (a complaint, a detailed pricing/financing \
question, or they ask for {settings.salesperson_name} by name), set status to \
needs_human and keep the reply short and reassuring rather than guessing.

One message at a time, one thought per message. Never invent prices, parts, or \
appointment availability you don't actually know. Always call the {TOOL_NAME} \
tool -- never reply in plain text."""


@dataclass
class CustomerAgentReply:
    reply_message: str
    status: str
    appointment_time: Optional[str] = None
    notes: Optional[str] = None


def _extract_tool_input(message) -> dict:
    for block in message.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return block.input
    raise RuntimeError(f"Model did not call {TOOL_NAME}; got: {message.content!r}")


def generate_customer_reply(customer: Customer, history: list[dict]) -> CustomerAgentReply:
    """`history` is oldest-first [{"role": "user"|"assistant", "content": str}],
    including the initial outreach text as the first assistant message."""
    if settings.mock_agent or not settings.anthropic_api_key:
        return _mock_reply(history)

    import anthropic  # deferred -- see app/agent/conversation.py

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=_system_prompt(customer),
        messages=history,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": TOOL_NAME},
    )
    data = _extract_tool_input(message)
    return CustomerAgentReply(
        reply_message=data["reply_message"],
        status=data.get("status", "responded"),
        appointment_time=data.get("appointment_time"),
        notes=data.get("notes"),
    )


# ---------------------------------------------------------------------------
# Mock agent -- same purpose as the one in conversation.py: exercise the
# whole pipeline (outreach loop, webhooks, DB, dashboard, notifications)
# with zero API keys.
# ---------------------------------------------------------------------------
_MOCK_SCRIPT = [
    CustomerAgentReply(
        reply_message="Great! Would mornings or afternoons work better this week?",
        status="responded",
    ),
    CustomerAgentReply(
        reply_message="Got it -- does Tuesday at 9:00 AM work for an oil change and tire rotation?",
        status="responded",
    ),
    CustomerAgentReply(
        reply_message="You're all set for Tuesday at 9:00 AM. See you then!",
        status="appointment_set",
        appointment_time="Tuesday, 9:00 AM",
        notes="Appointment confirmed via mock agent.",
    ),
]


def _mock_reply(history: list[dict]) -> CustomerAgentReply:
    customer_message_count = sum(1 for m in history if m["role"] == "user")
    index = min(max(customer_message_count - 1, 0), len(_MOCK_SCRIPT) - 1)
    return _MOCK_SCRIPT[index]
