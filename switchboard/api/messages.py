"""POST /v1/messages and friends.

Deliberately smaller than Twilio's Programmable Messaging surface: one
resource, JSON in and out, no separate "MessagingResponse" concept for
replies (see inbound.py -- replying is just calling this same send function).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from switchboard.auth import require_api_key
from switchboard.carriers import carrier
from switchboard.db import (
    ApiKey,
    create_message,
    get_message,
    is_opted_out,
    list_messages,
    update_message_status,
)

router = APIRouter(prefix="/v1/messages", tags=["messages"])


class SendMessageRequest(BaseModel):
    from_: Annotated[str, Field(alias="from")]
    to: str
    body: str

    model_config = {"populate_by_name": True}


def _message_to_dict(message) -> dict:
    return {
        "object": "message",
        "id": message.message_id,
        "direction": message.direction,
        "from": message.from_number,
        "to": message.to_number,
        "body": message.body,
        "status": message.status,
        "error_code": message.error_code,
        "error_message": message.error_message,
        "created_at": message.created_at,
        "updated_at": message.updated_at,
    }


@router.post("", status_code=201)
def send_message(req: SendMessageRequest, api_key: ApiKey = Depends(require_api_key)):
    if is_opted_out(req.to):
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "recipient_opted_out", "message": f"{req.to} has opted out and cannot be messaged."}},
        )

    try:
        result = carrier.send_sms(from_number=req.from_, to_number=req.to, body=req.body)
    except Exception as exc:
        message = create_message(
            direction="outbound", from_number=req.from_, to_number=req.to, body=req.body, status="failed",
        )
        update_message_status(message.message_id, "failed", error_message=str(exc))
        raise HTTPException(status_code=502, detail={"error": {"code": "carrier_error", "message": str(exc)}})

    message = create_message(
        direction="outbound", from_number=req.from_, to_number=req.to, body=req.body,
        status=result.status, carrier_message_id=result.carrier_message_id,
    )
    return _message_to_dict(message)


@router.get("/{message_id}")
def get_message_by_id(message_id: str, api_key: ApiKey = Depends(require_api_key)):
    message = get_message(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail={"error": {"code": "not_found", "message": "No such message."}})
    return _message_to_dict(message)


@router.get("")
def list_all_messages(
    to: str | None = None, from_: str | None = Query(default=None, alias="from"), limit: int = 50,
    api_key: ApiKey = Depends(require_api_key),
):
    messages = list_messages(limit=min(limit, 200), to_number=to, from_number=from_)
    return {"object": "list", "data": [_message_to_dict(m) for m in messages]}
