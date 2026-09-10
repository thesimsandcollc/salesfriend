"""POST /v1/calls and friends.

Twilio's equivalent needs a TwiML document (or a Studio flow) to say
anything on a call. Here, "what happens when they pick up" is two plain
JSON fields on the same request that started the call -- `say` and/or
`forward_to`. inbound.py's call-event handler is what actually issues those
Call Control actions once the carrier reports the call was answered.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from switchboard.auth import require_api_key
from switchboard.carriers import carrier
from switchboard.config import settings
from switchboard.db import ApiKey, create_call, get_call, list_calls, update_call

router = APIRouter(prefix="/v1/calls", tags=["calls"])


class CreateCallRequest(BaseModel):
    from_: Annotated[str, Field(alias="from")]
    to: str
    say: str | None = None
    forward_to: str | None = None
    status_webhook_url: str | None = None

    model_config = {"populate_by_name": True}


def _call_to_dict(call) -> dict:
    return {
        "object": "call",
        "id": call.call_id,
        "direction": call.direction,
        "from": call.from_number,
        "to": call.to_number,
        "status": call.status,
        "answered_by": call.answered_by,
        "duration_seconds": call.duration_seconds,
        "created_at": call.created_at,
        "updated_at": call.updated_at,
    }


@router.post("", status_code=201)
def create_outbound_call(req: CreateCallRequest, api_key: ApiKey = Depends(require_api_key)):
    if not req.say and not req.forward_to:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "missing_action", "message": "Provide at least one of `say` or `forward_to`."}},
        )
    if not settings.public_base_url:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "not_configured", "message": "SWITCHBOARD_PUBLIC_BASE_URL must be set so the carrier can reach this server's call-event webhook."}},
        )

    call = create_call(
        direction="outbound", from_number=req.from_, to_number=req.to, status="queued",
        say_message=req.say, forward_to=req.forward_to, status_webhook_url=req.status_webhook_url,
    )
    carrier_webhook_url = f"{settings.public_base_url}/carrier/{settings.carrier}/call-event"
    try:
        result = carrier.initiate_call(from_number=req.from_, to_number=req.to, webhook_url=carrier_webhook_url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": {"code": "carrier_error", "message": str(exc)}})

    call = update_call(call.call_id, status=result.status, carrier_call_id=result.carrier_call_id)
    return _call_to_dict(call)


@router.get("/{call_id}")
def get_call_by_id(call_id: str, api_key: ApiKey = Depends(require_api_key)):
    call = get_call(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "No such call."}})
    return _call_to_dict(call)


@router.get("")
def list_all_calls(limit: int = 50, api_key: ApiKey = Depends(require_api_key)):
    return {"object": "list", "data": [_call_to_dict(c) for c in list_calls(limit=min(limit, 200))]}
