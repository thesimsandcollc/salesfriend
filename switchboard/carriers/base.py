"""The one interface every route in this app depends on for actually
touching a phone network.

This is the same "swappable relay" idea as SalesFriend's
`app/telephony/base.py`, one layer further out: here it backs a whole
platform API instead of one app's two pipelines, and a real carrier
(Telnyx) implementation actually exists, not just Twilio.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CarrierSendResult:
    carrier_message_id: str
    status: str  # carrier-native status, e.g. "queued", "sent"


@dataclass
class InboundMessage:
    from_number: str
    to_number: str
    body: str
    carrier_message_id: Optional[str] = None


@dataclass
class MessageStatusEvent:
    carrier_message_id: str
    status: str  # normalized to: queued, sent, delivered, failed, undelivered
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class CarrierCallResult:
    carrier_call_id: str
    status: str


@dataclass
class CallEvent:
    carrier_call_id: str
    event: str  # initiated, answered, hangup
    call_status: Optional[str] = None  # normalized terminal status when event == "hangup"
    answered_by: Optional[str] = None


@dataclass
class AvailableNumber:
    phone_number: str
    region: Optional[str]
    monthly_cost_usd: Optional[str]


@dataclass
class OrderedNumber:
    phone_number: str
    order_id: str
    sms_capable: bool
    voice_capable: bool


class CarrierProvider(ABC):
    # -- Messaging ----------------------------------------------------
    @abstractmethod
    def send_sms(self, *, from_number: str, to_number: str, body: str) -> CarrierSendResult: ...

    @abstractmethod
    def parse_inbound_message(self, payload: dict) -> InboundMessage:
        """Turn a carrier's inbound-message webhook payload into our shape."""

    @abstractmethod
    def parse_message_status_event(self, payload: dict) -> Optional[MessageStatusEvent]:
        """Turn a carrier's delivery-status webhook payload into our shape.
        Returns None if this particular payload isn't a status update."""

    # -- Voice ----------------------------------------------------------
    @abstractmethod
    def initiate_call(
        self, *, from_number: str, to_number: str, webhook_url: str
    ) -> CarrierCallResult:
        """Start an outbound call. `webhook_url` is where the carrier will
        POST call lifecycle events (answered, hangup, ...) back to us."""

    @abstractmethod
    def speak(self, carrier_call_id: str, text: str) -> None:
        """Play text-to-speech on an in-progress call leg."""

    @abstractmethod
    def transfer(self, carrier_call_id: str, to_number: str) -> None:
        """Bridge an in-progress call leg to another number."""

    @abstractmethod
    def hangup(self, carrier_call_id: str) -> None:
        ...

    @abstractmethod
    def parse_call_event(self, payload: dict) -> Optional[CallEvent]:
        """Turn a carrier's call-control webhook payload into our shape.
        Returns None if this particular payload isn't one we act on."""

    # -- Numbers ----------------------------------------------------------
    @abstractmethod
    def list_available_numbers(self, *, area_code: Optional[str], country: str) -> list[AvailableNumber]:
        ...

    @abstractmethod
    def order_number(self, phone_number: str) -> OrderedNumber:
        ...

    # -- Webhook security ----------------------------------------------------------
    @abstractmethod
    def verify_webhook_signature(self, *, headers: dict, body: bytes) -> bool:
        """Verify that an inbound webhook actually came from the carrier."""
