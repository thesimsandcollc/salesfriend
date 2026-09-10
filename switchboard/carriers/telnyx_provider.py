"""The only file in this project that talks to Telnyx.

Telnyx was picked as the reference real carrier (over sticking with Twilio)
for three concrete reasons that back the "cheaper, simpler, no lock-in"
pitch: per-message/per-minute pricing that's typically 20-40% below Twilio's
list price, a JSON Call Control API instead of TwiML's XML markup, and
Ed25519-signed webhooks (verified below) instead of HMAC-SHA1. None of that
is exposed outside this file -- everything else in this app depends only on
carriers.base.CarrierProvider, so swapping to Bandwidth/Plivo/Vonage later
means writing one new file, not touching routes or storage.

API reference: https://developers.telnyx.com/docs/api/v2/overview
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

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
from switchboard.config import settings

log = logging.getLogger("switchboard.telnyx")

_BASE_URL = "https://api.telnyx.com/v2"

# Normalizes Telnyx's message delivery statuses to the small vocabulary the
# rest of this app uses (queued, sent, delivered, failed, undelivered).
_MESSAGE_STATUS_MAP = {
    "queued": "queued",
    "sending": "sent",
    "sent": "sent",
    "delivered": "delivered",
    "delivery_failed": "failed",
    "delivery_unconfirmed": "undelivered",
    "webhook_delivered": "delivered",
    "webhook_failed": "failed",
}


class TelnyxProvider(CarrierProvider):
    def __init__(self) -> None:
        self._client: Optional[httpx.Client] = None  # lazy: importing this module
        # shouldn't require real credentials (matches TwilioProvider's pattern).

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=_BASE_URL,
                headers={
                    "Authorization": f"Bearer {settings.telnyx_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    def _post(self, path: str, json: dict) -> dict:
        response = self.client.post(path, json=json)
        response.raise_for_status()
        return response.json()

    # -- Messaging ----------------------------------------------------

    def send_sms(self, *, from_number: str, to_number: str, body: str) -> CarrierSendResult:
        payload = {"from": from_number, "to": to_number, "text": body}
        if settings.telnyx_messaging_profile_id:
            payload["messaging_profile_id"] = settings.telnyx_messaging_profile_id
        data = self._post("/messages", payload)["data"]
        to_status = (data.get("to") or [{}])[0].get("status", "queued")
        return CarrierSendResult(
            carrier_message_id=data["id"],
            status=_MESSAGE_STATUS_MAP.get(to_status, to_status),
        )

    def parse_inbound_message(self, payload: dict) -> InboundMessage:
        message = payload["data"]["payload"]
        return InboundMessage(
            from_number=message["from"]["phone_number"],
            to_number=message["to"][0]["phone_number"],
            body=message.get("text", ""),
            carrier_message_id=message.get("id"),
        )

    def parse_message_status_event(self, payload: dict) -> Optional[MessageStatusEvent]:
        event_type = payload.get("data", {}).get("event_type", "")
        if event_type not in ("message.sent", "message.finalized"):
            return None
        message = payload["data"]["payload"]
        to_leg = (message.get("to") or [{}])[0]
        status = to_leg.get("status", "sent")
        errors = message.get("errors") or []
        return MessageStatusEvent(
            carrier_message_id=message["id"],
            status=_MESSAGE_STATUS_MAP.get(status, status),
            error_code=str(errors[0]["code"]) if errors else None,
            error_message=errors[0].get("title") if errors else None,
        )

    # -- Voice ----------------------------------------------------------

    def initiate_call(self, *, from_number: str, to_number: str, webhook_url: str) -> CarrierCallResult:
        data = self._post(
            "/calls",
            {
                "connection_id": settings.telnyx_connection_id,
                "from": from_number,
                "to": to_number,
                "webhook_url": webhook_url,
            },
        )["data"]
        return CarrierCallResult(carrier_call_id=data["call_control_id"], status="initiated")

    def speak(self, carrier_call_id: str, text: str) -> None:
        self._post(
            f"/calls/{carrier_call_id}/actions/speak",
            {"payload": text, "voice": "female", "language": "en-US"},
        )

    def transfer(self, carrier_call_id: str, to_number: str) -> None:
        self._post(f"/calls/{carrier_call_id}/actions/transfer", {"to": to_number})

    def hangup(self, carrier_call_id: str) -> None:
        self._post(f"/calls/{carrier_call_id}/actions/hangup", {})

    def parse_call_event(self, payload: dict) -> Optional[CallEvent]:
        event_type = payload.get("data", {}).get("event_type", "")
        call = payload["data"]["payload"]
        carrier_call_id = call.get("call_control_id")
        if not carrier_call_id:
            return None
        if event_type == "call.initiated":
            return CallEvent(carrier_call_id=carrier_call_id, event="initiated")
        if event_type == "call.answered":
            return CallEvent(carrier_call_id=carrier_call_id, event="answered")
        if event_type == "call.hangup":
            return CallEvent(
                carrier_call_id=carrier_call_id, event="hangup",
                call_status=call.get("hangup_cause", "unknown"),
            )
        return None

    # -- Numbers ----------------------------------------------------------

    def list_available_numbers(self, *, area_code: Optional[str], country: str) -> list[AvailableNumber]:
        params = {"filter[country_code]": country, "filter[limit]": "10"}
        if area_code:
            params["filter[national_destination_code]"] = area_code
        response = self.client.get("/available_phone_numbers", params=params)
        response.raise_for_status()
        results = []
        for item in response.json().get("data", []):
            cost = (item.get("cost_information") or {}).get("monthly_cost")
            regions = item.get("region_information") or []
            region = ", ".join(r.get("region_name", "") for r in regions) or None
            results.append(AvailableNumber(phone_number=item["phone_number"], region=region, monthly_cost_usd=cost))
        return results

    def order_number(self, phone_number: str) -> OrderedNumber:
        data = self._post("/number_orders", {"phone_numbers": [{"phone_number": phone_number}]})["data"]
        ordered = data["phone_numbers"][0]
        return OrderedNumber(
            phone_number=ordered["phone_number"], order_id=data["id"],
            sms_capable=True, voice_capable=True,
        )

    # -- Webhook security ----------------------------------------------------------

    def verify_webhook_signature(self, *, headers: dict, body: bytes) -> bool:
        """Telnyx signs webhooks with Ed25519 (telnyx-signature-ed25519 +
        telnyx-timestamp headers) rather than Twilio's HMAC-SHA1 -- see
        https://developers.telnyx.com/docs/api/v2/webhooks/receiving-webhooks
        Requires TELNYX_PUBLIC_KEY (from the Telnyx dashboard, not your API key)."""
        if not settings.telnyx_public_key:
            log.warning("TELNYX_PUBLIC_KEY not set -- skipping webhook signature verification")
            return True
        signature = headers.get("telnyx-signature-ed25519")
        timestamp = headers.get("telnyx-timestamp")
        if not signature or not timestamp:
            return False
        try:
            from nacl.exceptions import BadSignatureError
            from nacl.signing import VerifyKey

            verify_key = VerifyKey(base64.b64decode(settings.telnyx_public_key))
            verify_key.verify(f"{timestamp}|".encode() + body, base64.b64decode(signature))
            return True
        except BadSignatureError:
            return False
        except Exception:
            log.exception("Error verifying Telnyx webhook signature")
            return False
