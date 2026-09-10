"""STOP/HELP/START handling -- built into the platform, not left to callers.

This is one of the concrete "better than Twilio" bets: Twilio's opt-out
handling (Advanced Opt-Out) is a per-messaging-service setting you have to
know to turn on and configure. Here, every inbound message runs through
`classify_keyword` before it ever reaches your webhook, and the opt-out list
is enforced on every outbound send in api/messages.py -- there is no way to
accidentally text someone who said STOP through this API.
"""
from __future__ import annotations

# The standard CTIA/carrier-recognized keywords, both directions.
STOP_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_KEYWORDS = {"start", "yes", "unstop"}
HELP_KEYWORDS = {"help", "info"}

STOP_CONFIRMATION = (
    "You've been unsubscribed and won't receive further messages. Reply START to resubscribe."
)
START_CONFIRMATION = "You're resubscribed and will receive messages again."
HELP_RESPONSE = "This number sends automated messages. Reply STOP to unsubscribe."


def classify_keyword(body: str) -> str | None:
    """Returns 'stop', 'start', 'help', or None."""
    normalized = body.strip().strip(".!").lower()
    if normalized in STOP_KEYWORDS:
        return "stop"
    if normalized in START_KEYWORDS:
        return "start"
    if normalized in HELP_KEYWORDS:
        return "help"
    return None
