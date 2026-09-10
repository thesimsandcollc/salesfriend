"""Register a URL to receive event notifications.

Unlike an API key, the signing secret here isn't a bearer credential for
this API -- it only lets the *receiving* app verify a delivery really came
from this server (see webhooks.sign_payload) -- so it's fine to keep showing
it on GET, the way Stripe does for its webhook endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from switchboard.auth import require_api_key
from switchboard.db import ApiKey, create_webhook_endpoint, deactivate_webhook_endpoint, list_webhook_endpoints

router = APIRouter(prefix="/v1/webhook_endpoints", tags=["webhook_endpoints"])

# The event types this platform can emit. "all" subscribes to every one of
# them, including any added later.
EVENT_TYPES = (
    "message.received",
    "message.status_updated",
    "message.opted_out",
    "message.opted_in",
    "call.status_updated",
)


class CreateWebhookEndpointRequest(BaseModel):
    url: str
    event_types: str = "all"


def _endpoint_to_dict(endpoint) -> dict:
    return {
        "object": "webhook_endpoint",
        "id": endpoint.endpoint_id,
        "url": endpoint.url,
        "signing_secret": endpoint.signing_secret,
        "event_types": endpoint.event_types,
        "active": endpoint.active,
        "created_at": endpoint.created_at,
    }


@router.post("", status_code=201)
def create_endpoint(req: CreateWebhookEndpointRequest, api_key: ApiKey = Depends(require_api_key)):
    if req.event_types != "all":
        requested = {e.strip() for e in req.event_types.split(",")}
        unknown = requested - set(EVENT_TYPES)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "unknown_event_type", "message": f"Unknown event type(s): {', '.join(sorted(unknown))}"}},
            )
    endpoint = create_webhook_endpoint(req.url, req.event_types)
    return _endpoint_to_dict(endpoint)


@router.get("")
def list_endpoints(api_key: ApiKey = Depends(require_api_key)):
    return {"object": "list", "data": [_endpoint_to_dict(e) for e in list_webhook_endpoints()]}


@router.delete("/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: str, api_key: ApiKey = Depends(require_api_key)):
    deactivate_webhook_endpoint(endpoint_id)
