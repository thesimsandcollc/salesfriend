"""Public-facing IDs for API resources.

Twilio-style prefixed IDs (SM..., CA..., ...) are a genuinely good idea --
you can tell what kind of object an ID is just by looking at it, and it
can't collide with another resource type's ID space. Keeping that, dropping
everything else.
"""
from __future__ import annotations

import secrets

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _random_suffix(length: int = 24) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def random_token(length: int = 12) -> str:
    """Public helper for callers (e.g. the mock carrier) that just need a
    short random identifier, not one of the typed IDs below."""
    return _random_suffix(length)


def new_message_id() -> str:
    return f"SM{_random_suffix()}"


def new_call_id() -> str:
    return f"CA{_random_suffix()}"


def new_number_order_id() -> str:
    return f"PN{_random_suffix()}"


def new_webhook_endpoint_id() -> str:
    return f"WH{_random_suffix()}"


def new_webhook_delivery_id() -> str:
    return f"WD{_random_suffix()}"


def new_api_key() -> str:
    return f"sk_live_{_random_suffix(32)}"


def new_signing_secret() -> str:
    return f"whsec_{_random_suffix(32)}"
