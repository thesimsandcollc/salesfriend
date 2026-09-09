"""SalesFriend prototype -- entry point.

Two pipelines share this one app:

  Missed-call catch (unchanged from the original prototype):
    POST /voice/incoming      Twilio hits this the instant a customer calls
                               the business number. We ring the salesperson's
                               real cell.
    POST /voice/dial-status   Twilio hits this after that ring attempt ends.
                               If it wasn't answered, we send the auto-text
                               and the AI conversation begins from there.

  Proactive service reminders (new):
    An asyncio background loop (app/outreach.py) periodically texts
    customers who are due for a check-in, based on what's entered on
    /customers. Nothing here waits on Twilio to initiate it.

  Shared:
    POST /sms/incoming        Every reply -- from a lead OR an existing
                               customer -- comes back here. STOP is checked
                               first, then we look up which pipeline this
                               phone number belongs to.

Run with:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.agent.conversation import generate_reply
from app.agent.customer_conversation import generate_customer_reply
from app.compliance import is_opt_out
from app.config import settings
from app.dashboard.routes import router as dashboard_router
from app.outreach import outreach_loop
from app.storage.db import (
    add_customer_message,
    add_message,
    get_customer_by_phone,
    get_customer_message_history,
    get_message_history,
    get_or_create_lead,
    init_db,
    now_iso,
    update_customer_fields,
    update_lead_fields,
)
from app.telephony import provider

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("salesfriend")

app = FastAPI(title="SalesFriend")
app.include_router(dashboard_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("SalesFriend up. Mock agent: %s", settings.mock_agent or not settings.anthropic_api_key)
    if not settings.dashboard_password:
        log.warning(
            "DASHBOARD_PASSWORD is not set -- /dashboard is open to anyone who "
            "can reach this server. Fine on localhost, not fine once deployed."
        )
    asyncio.create_task(outreach_loop())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    """A customer just called the business number. Ring the salesperson's
    real cell; if it isn't answered within RING_TIMEOUT_SECONDS, Twilio will
    call /voice/dial-status next."""
    status_callback_url = f"{settings.public_base_url}/voice/dial-status"
    twiml = provider.build_incoming_call_response(
        forward_to=settings.salesperson_phone_number,
        timeout_seconds=settings.ring_timeout_seconds,
        status_callback_url=status_callback_url,
    )
    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/dial-status")
async def voice_dial_status(request: Request):
    """The ring attempt to the salesperson's cell just finished one way or
    another. If it wasn't picked up, this is the moment the call became a
    'missed call' -- send the auto-text and open the lead."""
    form = dict(await request.form())
    dial_status = provider.parse_dial_status(form)

    if not dial_status.was_answered:
        lead = get_or_create_lead(dial_status.caller)
        greeting = (
            f"Hi! This is {settings.dealership_name} -- sorry we missed your call! "
            f"I'm {settings.salesperson_name}'s assistant. What can I help you find today?"
        )
        provider.send_sms(to=dial_status.caller, body=greeting)
        add_message(lead.id, "out", greeting)
        update_lead_fields(dial_status.caller, status="texted")
        log.info("Missed call from %s -- auto-text sent", dial_status.caller)

    twiml = provider.build_missed_call_voice_response()
    return Response(content=twiml, media_type="application/xml")


@app.post("/sms/incoming")
async def sms_incoming(request: Request):
    """Every inbound text, from either pipeline, arrives here. STOP (and the
    other standard opt-out keywords) is handled first and short-circuits
    everything else, regardless of which list the number is on."""
    form = dict(await request.form())
    incoming = provider.parse_incoming_sms(form)

    customer = get_customer_by_phone(incoming.from_number)

    if is_opt_out(incoming.body):
        confirmation = f"You've been unsubscribed from {settings.dealership_name} texts and won't receive further messages."
        _handle_opt_out(incoming.from_number, incoming.body, confirmation, customer)
        twiml = provider.build_sms_reply_response(confirmation)
        return Response(content=twiml, media_type="application/xml")

    if customer is not None:
        twiml = _handle_customer_reply(customer, incoming.body)
        return Response(content=twiml, media_type="application/xml")

    twiml = _handle_lead_reply(incoming.from_number, incoming.body)
    return Response(content=twiml, media_type="application/xml")


def _handle_opt_out(phone: str, body: str, confirmation: str, customer) -> None:
    if customer is not None:
        add_customer_message(customer.id, "in", body)
        add_customer_message(customer.id, "out", confirmation)
        update_customer_fields(phone, status="opted_out", last_contacted_at=now_iso())
        log.info("Customer %s opted out", phone)
    else:
        lead = get_or_create_lead(phone)
        add_message(lead.id, "in", body)
        add_message(lead.id, "out", confirmation)
        update_lead_fields(phone, status="opted_out")
        log.info("Lead %s opted out", phone)


def _handle_lead_reply(phone: str, body: str) -> str:
    """The sales-lead conversation -- unchanged from the original prototype."""
    lead = get_or_create_lead(phone)
    previous_status = lead.status
    add_message(lead.id, "in", body)

    history = get_message_history(lead.id)
    reply = generate_reply(lead, history)

    add_message(lead.id, "out", reply.reply_message)
    lead = update_lead_fields(
        phone,
        vehicle_interest=reply.vehicle_interest,
        trade_in=reply.trade_in,
        appointment_time=reply.appointment_time,
        status=reply.status,
        notes=reply.notes,
    )

    if settings.notify_salesperson and reply.status != previous_status:
        _notify_salesperson_about_lead(lead, reply.status)

    return provider.build_sms_reply_response(reply.reply_message)


def _handle_customer_reply(customer, body: str) -> str:
    """The service-reminder conversation -- an existing customer replying to
    an outreach text (or a follow-up in that same thread)."""
    previous_status = customer.status
    add_customer_message(customer.id, "in", body)

    history = get_customer_message_history(customer.id)
    reply = generate_customer_reply(customer, history)

    add_customer_message(customer.id, "out", reply.reply_message)
    customer = update_customer_fields(
        customer.phone,
        appointment_time=reply.appointment_time,
        status=reply.status,
        notes=reply.notes,
        last_contacted_at=now_iso(),
    )

    if settings.notify_salesperson and reply.status != previous_status:
        _notify_salesperson_about_customer(customer, reply.status)

    return provider.build_sms_reply_response(reply.reply_message)


def _notify_salesperson_about_lead(lead, new_status: str) -> None:
    if new_status == "appointment_set":
        text = (
            f"SalesFriend: {lead.name or lead.phone} booked {lead.appointment_time or 'an appointment'} "
            f"-- interested in {lead.vehicle_interest or 'a vehicle'}."
        )
    elif new_status == "needs_human":
        text = f"SalesFriend: {lead.name or lead.phone} needs you directly -- {lead.notes or 'check the dashboard'}."
    else:
        return
    _send_notification(text)


def _notify_salesperson_about_customer(customer, new_status: str) -> None:
    if new_status == "appointment_set":
        text = f"SalesFriend: {customer.name} booked a service appointment -- {customer.appointment_time or 'time TBD'}."
    elif new_status == "needs_human":
        text = f"SalesFriend: {customer.name} needs you directly -- {customer.notes or 'check the dashboard'}."
    else:
        return
    _send_notification(text)


def _send_notification(text: str) -> None:
    try:
        provider.send_sms(to=settings.salesperson_phone_number, body=text)
    except Exception:  # pragma: no cover - notification failures shouldn't break either pipeline
        log.exception("Failed to notify salesperson")
