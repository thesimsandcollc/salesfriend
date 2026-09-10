"""Everything the carrier posts back to us lands here:

  POST /carrier/{carrier}/inbound-message   Someone texted one of our numbers.
  POST /carrier/{carrier}/message-status    Delivery status for a message we sent.
  POST /carrier/{carrier}/call-event        A call we started changed state.

These are carrier-facing webhooks, not the platform's own outbound
notifications (see webhooks.py for those) -- this module's job is to
normalize whatever the carrier sent, apply compliance (STOP/HELP/START),
update our own records, and then call emit_event() so subscribed developer
webhooks find out about it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from switchboard.carriers import carrier
from switchboard.compliance import (
    HELP_RESPONSE,
    START_CONFIRMATION,
    STOP_CONFIRMATION,
    classify_keyword,
)
from switchboard.config import settings
from switchboard.db import (
    add_opt_out,
    create_message,
    get_call_by_carrier_id,
    get_message_by_carrier_id,
    remove_opt_out,
    update_call,
    update_message_status,
)
from switchboard.webhooks import emit_event

log = logging.getLogger("switchboard.inbound")

router = APIRouter(prefix="/carrier/{carrier_name}", tags=["carrier webhooks"])


async def _verify(carrier_name: str, request: Request) -> bytes:
    if carrier_name != settings.carrier:
        raise HTTPException(status_code=404, detail="No such carrier configured")
    body = await request.body()
    if not carrier.verify_webhook_signature(headers=dict(request.headers), body=body):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    return body


@router.post("/inbound-message")
async def inbound_message(carrier_name: str, request: Request):
    body = await _verify(carrier_name, request)
    payload = await request.json() if body else {}
    inbound = carrier.parse_inbound_message(payload)

    keyword = classify_keyword(inbound.body)
    if keyword == "stop":
        add_opt_out(inbound.from_number)
        reply_body = STOP_CONFIRMATION
        event_type = "message.opted_out"
    elif keyword == "start":
        remove_opt_out(inbound.from_number)
        reply_body = START_CONFIRMATION
        event_type = "message.opted_in"
    elif keyword == "help":
        reply_body = HELP_RESPONSE
        event_type = "message.received"
    else:
        reply_body = None
        event_type = "message.received"

    message = create_message(
        direction="inbound", from_number=inbound.from_number, to_number=inbound.to_number,
        body=inbound.body, status="received", carrier_message_id=inbound.carrier_message_id,
    )

    emit_event(event_type, {
        "id": message.message_id, "from": message.from_number, "to": message.to_number,
        "body": message.body, "created_at": message.created_at,
    })

    # STOP/HELP/START get an automatic reply on this same number -- the whole
    # point of built-in compliance is that a developer's app never has to
    # remember to wire this up itself.
    if reply_body is not None:
        try:
            carrier.send_sms(from_number=inbound.to_number, to_number=inbound.from_number, body=reply_body)
        except Exception:
            log.exception("Failed to send automatic %s reply to %s", keyword, inbound.from_number)

    return {"status": "ok"}


@router.post("/message-status")
async def message_status(carrier_name: str, request: Request):
    body = await _verify(carrier_name, request)
    payload = await request.json() if body else {}
    event = carrier.parse_message_status_event(payload)
    if event is None:
        return {"status": "ignored"}

    message = get_message_by_carrier_id(event.carrier_message_id)
    if message is None:
        log.warning("Status event for unknown carrier_message_id=%s", event.carrier_message_id)
        return {"status": "ignored"}

    message = update_message_status(
        message.message_id, event.status, error_code=event.error_code, error_message=event.error_message,
    )
    emit_event("message.status_updated", {
        "id": message.message_id, "status": message.status,
        "error_code": message.error_code, "error_message": message.error_message,
    })
    return {"status": "ok"}


@router.post("/call-event")
async def call_event(carrier_name: str, request: Request):
    body = await _verify(carrier_name, request)
    payload = await request.json() if body else {}
    event = carrier.parse_call_event(payload)
    if event is None:
        return {"status": "ignored"}

    call = get_call_by_carrier_id(event.carrier_call_id)
    if call is None:
        log.warning("Call event for unknown carrier_call_id=%s", event.carrier_call_id)
        return {"status": "ignored"}

    if event.event == "answered":
        call = update_call(call.call_id, status="in-progress")
        # This is the payoff of storing say/forward_to on the call itself
        # (api/calls.py): no separate "call flow" document to look up.
        if call.say_message:
            carrier.speak(event.carrier_call_id, call.say_message)
        if call.forward_to:
            carrier.transfer(event.carrier_call_id, call.forward_to)
    elif event.event == "hangup":
        call = update_call(call.call_id, status=event.call_status or "completed")
        emit_event("call.status_updated", {"id": call.call_id, "status": call.status})

    return {"status": "ok"}
