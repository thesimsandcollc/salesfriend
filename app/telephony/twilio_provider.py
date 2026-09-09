"""The only file in this project that imports the Twilio SDK.

If you ever swap relay providers, this is the file that gets replaced --
everything else talks to app.telephony.base.RelayProvider instead.
"""
from __future__ import annotations

from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Dial, VoiceResponse

from app.config import settings
from app.telephony.base import DialStatus, IncomingSms, RelayProvider


class TwilioProvider(RelayProvider):
    def __init__(self) -> None:
        self._client: Client | None = None  # created lazily so importing this
        # module doesn't require real credentials (useful for MOCK_AGENT runs
        # and for the simulate_conversation.py script).

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        return self._client

    def send_sms(self, to: str, body: str) -> None:
        self.client.messages.create(from_=settings.twilio_phone_number, to=to, body=body)

    def parse_incoming_sms(self, form: dict) -> IncomingSms:
        return IncomingSms(from_number=form["From"], to_number=form["To"], body=form.get("Body", ""))

    def parse_dial_status(self, form: dict) -> DialStatus:
        return DialStatus(
            caller=form["From"],
            callee=form.get("DialCallTo", settings.salesperson_phone_number),
            call_status=form.get("DialCallStatus", "unknown"),
        )

    def build_incoming_call_response(self, forward_to: str, timeout_seconds: int, status_callback_url: str) -> str:
        response = VoiceResponse()
        dial = Dial(timeout=timeout_seconds, action=status_callback_url, method="POST")
        dial.number(forward_to)
        response.append(dial)
        return str(response)

    def build_missed_call_voice_response(self) -> str:
        response = VoiceResponse()
        response.say("Thanks for calling. We just sent you a text -- talk soon!")
        response.hangup()
        return str(response)

    def build_sms_reply_response(self, body: str) -> str:
        response = MessagingResponse()
        response.message(body)
        return str(response)
