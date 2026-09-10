"""Switchboard -- a self-hosted, developer-first alternative to Twilio's
core Programmable Messaging + Voice products.

  /v1/messages, /v1/calls, /v1/numbers, /v1/webhook_endpoints
                             The REST API a developer's app calls (see
                             switchboard/client/python_client.py for a tiny
                             SDK wrapping it).
  /carrier/{carrier}/*       Where the underlying carrier (Telnyx, or the
                             built-in mock) posts events back to us.
  /dashboard                 Activity log, API keys, webhook endpoints.

Run with:  uvicorn switchboard.main:app --reload --port 8001
(a different port from SalesFriend's app.main, so both can run side by side.)
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from switchboard.api.calls import router as calls_router
from switchboard.api.messages import router as messages_router
from switchboard.api.numbers import router as numbers_router
from switchboard.api.webhook_endpoints import router as webhook_endpoints_router
from switchboard.config import settings
from switchboard.dashboard.routes import router as dashboard_router
from switchboard.db import count_active_api_keys, create_api_key, init_db
from switchboard.inbound import router as inbound_router
from switchboard.webhooks import webhook_retry_loop

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("switchboard")

app = FastAPI(title="Switchboard", version="0.1.0")
app.include_router(messages_router)
app.include_router(calls_router)
app.include_router(numbers_router)
app.include_router(webhook_endpoints_router)
app.include_router(inbound_router)
app.include_router(dashboard_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    log.info("Switchboard up. Carrier: %s", settings.carrier)

    if count_active_api_keys() == 0:
        _, plaintext = create_api_key(settings.bootstrap_api_key_label)
        log.warning(
            "No API keys existed yet -- created one so the API is usable: %s "
            "(shown once; store it now, e.g. `export SWITCHBOARD_API_KEY=%s`)",
            plaintext, plaintext,
        )

    if not settings.dashboard_password:
        log.warning(
            "SWITCHBOARD_DASHBOARD_PASSWORD is not set -- /dashboard is open to "
            "anyone who can reach this server. Fine on localhost, not once deployed."
        )
    if settings.carrier == "telnyx" and not settings.public_base_url:
        log.warning(
            "SWITCHBOARD_PUBLIC_BASE_URL is not set -- Telnyx has no way to reach "
            "this server's /carrier/telnyx/* webhooks or make outbound calls."
        )

    asyncio.create_task(webhook_retry_loop())


@app.get("/health")
def health():
    return {"status": "ok", "carrier": settings.carrier}
