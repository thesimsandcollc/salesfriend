"""The actual "agentic" part: given a lead's conversation so far, decide what
to text back AND update what we know about them, in a single model call.

We force the model to call one tool every turn (`respond_to_lead`) rather
than freeform-chatting, so the reply text and the structured fields always
arrive together and the dashboard is never out of sync with the
conversation. If ANTHROPIC_API_KEY isn't set (or MOCK_AGENT=true), a scripted
mock stands in so the rest of the system can be tested without API access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.storage.db import Lead

# `anthropic` is imported lazily inside generate_reply(), not here -- so that
# MOCK_AGENT mode (and this whole module) works even before `pip install
# anthropic` has happened, and a missing/blank API key degrades to the mock
# instead of crashing on import.

TOOL_NAME = "respond_to_lead"

TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Compose the next SMS reply to a car-dealership lead who called and "
        "didn't get through, and record whatever new information this turn "
        "revealed about them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reply_message": {
                "type": "string",
                "description": (
                    "The SMS to send back. Warm, brief, one question at a time, "
                    "under ~300 characters. No markdown, no bullet points -- this "
                    "is a text message."
                ),
            },
            "vehicle_interest": {
                "type": "string",
                "description": "What vehicle(s) they're interested in, if known so far (e.g. '2023 Honda CR-V EX-L'). Omit if not yet known.",
            },
            "trade_in": {
                "type": "string",
                "description": "Their trade-in vehicle and any details mentioned (model, mileage), if any. Omit if not applicable or not yet known.",
            },
            "appointment_time": {
                "type": "string",
                "description": "The appointment day/time as agreed or currently proposed (e.g. 'Sat 9/6, 10:00 AM'). Omit if none discussed yet.",
            },
            "status": {
                "type": "string",
                "enum": ["new", "texted", "appointment_set", "needs_human"],
                "description": (
                    "'texted' = conversation in progress. 'appointment_set' = a "
                    "specific time has been agreed. 'needs_human' = the lead asked "
                    "for something this bot shouldn't handle alone (price "
                    "negotiation, a complaint, explicitly asking for the "
                    "salesperson) -- in that case reply_message should be short "
                    "and reassuring, not another qualifying question."
                ),
            },
            "notes": {
                "type": "string",
                "description": "A short internal summary update for the salesperson, one sentence.",
            },
        },
        "required": ["reply_message", "status"],
    },
}


def _system_prompt() -> str:
    return f"""You are SalesFriend, texting on behalf of {settings.salesperson_name} at \
{settings.dealership_name} with a customer whose call just went unanswered.

Your job, in order:
1. Reassure them briefly that someone saw their call.
2. Find out what vehicle they're interested in (new or used, make/model).
3. Ask about a trade-in if relevant.
4. Get a rough budget or timeline only if it comes up naturally -- don't interrogate.
5. Propose a specific appointment day/time and get them to agree to one.

Rules:
- One question per message. Keep it conversational, not a form.
- Never invent inventory, prices, or financing terms you don't know.
- If they ask for something you can't responsibly answer yourself (final \
pricing, financing approval, a complaint, or they just ask to talk to \
{settings.salesperson_name} directly), set status to needs_human and reply \
with something short like "{settings.salesperson_name} will follow up with \
you directly shortly" -- do not keep asking qualifying questions once that's \
happened.
- Always call the {TOOL_NAME} tool. Never reply in plain text."""


@dataclass
class AgentReply:
    reply_message: str
    status: str
    vehicle_interest: Optional[str] = None
    trade_in: Optional[str] = None
    appointment_time: Optional[str] = None
    notes: Optional[str] = None


def _extract_tool_input(message) -> dict:
    for block in message.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return block.input
    raise RuntimeError(f"Model did not call {TOOL_NAME}; got: {message.content!r}")


def generate_reply(lead: Lead, history: list[dict]) -> AgentReply:
    """`history` is oldest-first [{"role": "user"|"assistant", "content": str}],
    NOT including the lead's just-received message being replied to -- callers
    should append that before calling this."""
    if settings.mock_agent or not settings.anthropic_api_key:
        return _mock_reply(history)

    import anthropic  # deferred -- see note at top of file

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=_system_prompt(),
        messages=history,
        tools=[TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": TOOL_NAME},
    )
    data = _extract_tool_input(message)
    return AgentReply(
        reply_message=data["reply_message"],
        status=data.get("status", "texted"),
        vehicle_interest=data.get("vehicle_interest"),
        trade_in=data.get("trade_in"),
        appointment_time=data.get("appointment_time"),
        notes=data.get("notes"),
    )


# ---------------------------------------------------------------------------
# Mock agent -- lets the whole pipeline (webhooks, DB, dashboard, salesperson
# notifications) be exercised with zero API keys and zero Twilio account.
# It just walks through a fixed script based on how many lead messages have
# come in so far.
# ---------------------------------------------------------------------------
_MOCK_SCRIPT = [
    AgentReply(
        reply_message="Thanks for calling! Sorry we missed you. What kind of vehicle are you looking for?",
        status="texted",
    ),
    AgentReply(
        reply_message="Got it. Do you have a vehicle you'd want to trade in?",
        status="texted",
    ),
    AgentReply(
        reply_message="Good to know. Would Saturday morning or a weekday evening work better for a test drive?",
        status="texted",
        trade_in="mentioned, details pending",
    ),
    AgentReply(
        reply_message="Great -- does 10:00 AM Saturday work? I'll have the numbers ready.",
        status="texted",
    ),
    AgentReply(
        reply_message="You're all set for Saturday at 10:00 AM. See you then!",
        status="appointment_set",
        appointment_time="Saturday, 10:00 AM",
        notes="Appointment confirmed via mock agent.",
    ),
]


def _mock_reply(history: list[dict]) -> AgentReply:
    lead_message_count = sum(1 for m in history if m["role"] == "user")
    index = min(lead_message_count - 1, len(_MOCK_SCRIPT) - 1)
    index = max(index, 0)
    return _MOCK_SCRIPT[index]
