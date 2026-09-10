"""Picks the CarrierProvider implementation for this instance.

Nothing outside this package ever imports a specific carrier SDK directly --
api/, inbound.py, and webhooks.py all depend only on carriers.base.CarrierProvider.
To add a new carrier (Bandwidth, Plivo, Vonage, ...): implement that
interface in a new module here and add one branch below.
"""
from __future__ import annotations

from switchboard.config import settings


def _build_carrier():
    if settings.carrier == "telnyx":
        from switchboard.carriers.telnyx_provider import TelnyxProvider

        return TelnyxProvider()
    if settings.carrier == "mock":
        from switchboard.carriers.mock_provider import MockProvider

        return MockProvider()
    raise ValueError(f"Unknown SWITCHBOARD_CARRIER: {settings.carrier!r} (expected 'mock' or 'telnyx')")


carrier = _build_carrier()
