"""The one interface the rest of the app is allowed to depend on for
phones/texts.

This is the "swappable relay" the plan document talks about: today
TwilioProvider is the only implementation, but nothing in app/main.py or
app/agent ever imports Twilio directly. To move to Telnyx, Bandwidth, Plivo,
or an on-device Android relay later, implement this interface once and
change a single import in app/telephony/__init__.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class IncomingSms:
    from_number: str
    to_number: str
    body: str


@dataclass
class DialStatus:
    """What happened to the "ring the salesperson's cell" attempt."""

    caller: str          # the customer's number (who called in)
    callee: str           # the salesperson's cell
    call_status: str      # provider-native status string, e.g. "completed", "no-answer", "busy"

    @property
    def was_answered(self) -> bool:
        return self.call_status == "completed"


class RelayProvider(ABC):
    """A phone number that can ring a real cellphone and send/receive SMS,
    without the rest of the app needing to know which vendor is behind it."""

    @abstractmethod
    def send_sms(self, to: str, body: str) -> None:
        """Send a text from the business number to `to`."""

    @abstractmethod
    def parse_incoming_sms(self, form: dict) -> IncomingSms:
        """Turn a provider's inbound-SMS webhook payload into our own shape."""

    @abstractmethod
    def parse_dial_status(self, form: dict) -> DialStatus:
        """Turn a provider's post-dial-attempt webhook payload into our own shape."""

    @abstractmethod
    def build_incoming_call_response(self, forward_to: str, timeout_seconds: int, status_callback_url: str) -> str:
        """Return the response body (e.g. TwiML) that rings `forward_to` for
        up to `timeout_seconds`, then posts the result to `status_callback_url`."""

    @abstractmethod
    def build_missed_call_voice_response(self) -> str:
        """Return the response body for the caller's leg once we've decided
        the call was missed and the auto-text has been sent."""

    @abstractmethod
    def build_sms_reply_response(self, body: str) -> str:
        """Return the response body that replies to an inbound SMS inline
        (avoids a second outbound API call for the main conversation turn)."""
