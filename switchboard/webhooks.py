"""Outbound event delivery: tells a developer's app what just happened.

Every event (message.received, message.status_updated, call.status_updated,
message.opted_out, ...) is fanned out to every active webhook_endpoint that
wants it. Each attempt is recorded in webhook_deliveries so a developer can
see exactly what was sent, what came back, and why a delivery is still
retrying -- this is the "built-in deliverability" bet: Twilio's equivalent
(debugger + a manually-configured retry policy) is a separate product you
have to go look at, this is just a table in the same database.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from switchboard.config import settings
from switchboard.db import (
    WebhookDelivery,
    create_webhook_delivery,
    due_webhook_deliveries,
    list_webhook_endpoints,
    record_delivery_attempt,
)

log = logging.getLogger("switchboard.webhooks")

_TIMEOUT_SECONDS = 10.0
# Exponential-ish backoff: 30s, 2m, 10m, 30m, 1h, then abandon.
_RETRY_SCHEDULE_SECONDS = [30, 120, 600, 1800, 3600]


def sign_payload(secret: str, timestamp: str, body: str) -> str:
    message = f"{timestamp}.{body}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def emit_event(event_type: str, data: dict) -> None:
    """Fan an event out to every webhook endpoint subscribed to it. Queues a
    webhook_deliveries row per endpoint; the retry loop (below) does the
    actual HTTP delivery so a slow or dead endpoint never blocks the request
    that triggered the event (an inbound SMS webhook from the carrier, an
    API call, ...)."""
    payload = json.dumps({"event_type": event_type, "data": data})
    endpoints = [e for e in list_webhook_endpoints(active_only=True) if e.wants(event_type)]
    for endpoint in endpoints:
        create_webhook_delivery(endpoint.endpoint_id, event_type, payload)
    if not endpoints:
        log.debug("No webhook endpoint subscribed to %s -- event not delivered anywhere", event_type)


def _attempt_delivery(client: httpx.Client, delivery: WebhookDelivery, endpoint) -> None:
    timestamp = str(int(time.time()))
    signature = sign_payload(endpoint.signing_secret, timestamp, delivery.payload)
    try:
        response = client.post(
            endpoint.url,
            content=delivery.payload,
            headers={
                "Content-Type": "application/json",
                "Switchboard-Signature": f"t={timestamp},v1={signature}",
                "Switchboard-Event": delivery.event_type,
                "Switchboard-Delivery": delivery.delivery_id,
            },
            timeout=_TIMEOUT_SECONDS,
        )
        succeeded = 200 <= response.status_code < 300
        response_code = response.status_code
    except httpx.HTTPError as exc:
        succeeded = False
        response_code = None
        log.warning("Webhook delivery %s to %s failed: %s", delivery.delivery_id, endpoint.url, exc)

    if succeeded:
        record_delivery_attempt(delivery.delivery_id, status="delivered", response_code=response_code, next_attempt_at=None)
        return

    attempt_index = delivery.attempts  # 0-based count of attempts made *before* this one
    if attempt_index >= len(_RETRY_SCHEDULE_SECONDS) or delivery.attempts + 1 >= settings.webhook_retry_max_attempts:
        record_delivery_attempt(delivery.delivery_id, status="abandoned", response_code=response_code, next_attempt_at=None)
        log.error("Webhook delivery %s to %s abandoned after %d attempts", delivery.delivery_id, endpoint.url, delivery.attempts + 1)
        return

    delay = _RETRY_SCHEDULE_SECONDS[min(attempt_index, len(_RETRY_SCHEDULE_SECONDS) - 1)]
    next_attempt_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    record_delivery_attempt(delivery.delivery_id, status="failed", response_code=response_code, next_attempt_at=next_attempt_at)


def run_due_deliveries() -> int:
    """Sends every delivery that's pending or due for retry. Returns how
    many were attempted. Called both by the background loop (main.py) and
    on-demand from the dashboard."""
    deliveries = due_webhook_deliveries()
    if not deliveries:
        return 0
    endpoints_by_id = {e.endpoint_id: e for e in list_webhook_endpoints()}
    with httpx.Client() as client:
        for delivery in deliveries:
            endpoint = endpoints_by_id.get(delivery.endpoint_id)
            if endpoint is None or not endpoint.active:
                record_delivery_attempt(delivery.delivery_id, status="abandoned", response_code=None, next_attempt_at=None)
                continue
            _attempt_delivery(client, delivery, endpoint)
    return len(deliveries)


async def webhook_retry_loop() -> None:
    """Background task started at app startup -- see main.py. Runs
    run_due_deliveries() on an interval so a webhook endpoint that was down
    for a few minutes still gets every event once it's back up."""
    import asyncio

    while True:
        try:
            sent = run_due_deliveries()
            if sent:
                log.info("Webhook retry loop: attempted %d delivery(ies)", sent)
        except Exception:
            log.exception("Webhook retry loop iteration failed")
        await asyncio.sleep(settings.webhook_retry_check_interval_seconds)
