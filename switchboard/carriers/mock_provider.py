"""A carrier that doesn't touch a real phone network at all.

This is the default (`SWITCHBOARD_CARRIER=mock`) so the whole API --
sending "messages," making "calls," provisioning "numbers" -- works with
zero external accounts, zero credentials, and zero cost. It's the same
philosophy as SalesFriend's `MOCK_AGENT`: try the shape of the thing before
wiring up a real vendor.

Because there's no real carrier to call us back, inbound events (a reply
text, a call being answered) don't happen on their own here -- simulate them
by POSTing to /carrier/mock/inbound-message or /carrier/mock/call-event
(see scripts/simulate.py and the README).
"""
from __future__ import annotations

import logging
from typing import Optional

from switchboard.carriers.base import (
    AvailableNumber,
    CallEvent,
    CarrierCallResult,
    CarrierProvider,
    CarrierSendResult,
    InboundMessage,
    MessageStatusEvent,
    OrderedNumber,
)
from switchboard.ids import random_token

log = logging.getLogger("switchboard.mock")


class MockProvider(CarrierProvider):
    def send_sms(self, *, from_number: str, to_number: str, body: str) -> CarrierSendResult:
        carrier_message_id = f"mock-msg-{random_token(12)}"
        log.info("[mock carrier] SMS %s -> %s: %r", from_number, to_number, body)
        return CarrierSendResult(carrier_message_id=carrier_message_id, status="sent")

    def parse_inbound_message(self, payload: dict) -> InboundMessage:
        return InboundMessage(
            from_number=payload["from"],
            to_number=payload["to"],
            body=payload.get("body", ""),
            carrier_message_id=payload.get("carrier_message_id"),
        )

    def parse_message_status_event(self, payload: dict) -> Optional[MessageStatusEvent]:
        if "carrier_message_id" not in payload:
            return None
        return MessageStatusEvent(
            carrier_message_id=payload["carrier_message_id"],
            status=payload.get("status", "delivered"),
            error_code=payload.get("error_code"),
            error_message=payload.get("error_message"),
        )

    def initiate_call(self, *, from_number: str, to_number: str, webhook_url: str) -> CarrierCallResult:
        carrier_call_id = f"mock-call-{random_token(12)}"
        log.info("[mock carrier] Call %s -> %s (events would post to %s)", from_number, to_number, webhook_url)
        return CarrierCallResult(carrier_call_id=carrier_call_id, status="initiated")

    def speak(self, carrier_call_id: str, text: str) -> None:
        log.info("[mock carrier] Call %s speaks: %r", carrier_call_id, text)

    def transfer(self, carrier_call_id: str, to_number: str) -> None:
        log.info("[mock carrier] Call %s transferring to %s", carrier_call_id, to_number)

    def hangup(self, carrier_call_id: str) -> None:
        log.info("[mock carrier] Call %s hangup", carrier_call_id)

    def parse_call_event(self, payload: dict) -> Optional[CallEvent]:
        if "carrier_call_id" not in payload or "event" not in payload:
            return None
        return CallEvent(
            carrier_call_id=payload["carrier_call_id"],
            event=payload["event"],
            call_status=payload.get("call_status"),
            answered_by=payload.get("answered_by"),
        )

    def list_available_numbers(self, *, area_code: Optional[str], country: str) -> list[AvailableNumber]:
        prefix = f"+1{area_code}" if country == "US" and area_code else "+1555"
        return [
            AvailableNumber(phone_number=f"{prefix}{i:07d}", region="Mock Region", monthly_cost_usd="1.00")
            for i in range(1, 4)
        ]

    def order_number(self, phone_number: str) -> OrderedNumber:
        return OrderedNumber(
            phone_number=phone_number, order_id=f"mock-order-{random_token(8)}",
            sms_capable=True, voice_capable=True,
        )

    def verify_webhook_signature(self, *, headers: dict, body: bytes) -> bool:
        return True
