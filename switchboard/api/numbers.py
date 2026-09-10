"""Number search + provisioning.

Buying a real number still requires a funded carrier account -- this file
just makes the API for it uniform across carriers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from switchboard.auth import require_api_key
from switchboard.carriers import carrier
from switchboard.db import ApiKey, list_numbers, record_number

router = APIRouter(prefix="/v1/numbers", tags=["numbers"])


class OrderNumberRequest(BaseModel):
    phone_number: str


def _number_to_dict(number) -> dict:
    return {
        "object": "phone_number",
        "phone_number": number.phone_number,
        "carrier": number.carrier,
        "sms_capable": number.sms_capable,
        "voice_capable": number.voice_capable,
        "status": number.status,
        "created_at": number.created_at,
    }


@router.get("/available")
def available_numbers(area_code: str | None = None, country: str = "US", api_key: ApiKey = Depends(require_api_key)):
    try:
        results = carrier.list_available_numbers(area_code=area_code, country=country)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": {"code": "carrier_error", "message": str(exc)}})
    return {
        "object": "list",
        "data": [
            {"object": "available_phone_number", "phone_number": r.phone_number, "region": r.region, "monthly_cost_usd": r.monthly_cost_usd}
            for r in results
        ],
    }


@router.post("", status_code=201)
def order_number(req: OrderNumberRequest, api_key: ApiKey = Depends(require_api_key)):
    try:
        ordered = carrier.order_number(req.phone_number)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"error": {"code": "carrier_error", "message": str(exc)}})
    number = record_number(
        ordered.phone_number, carrier=type(carrier).__name__, sms_capable=ordered.sms_capable,
        voice_capable=ordered.voice_capable, order_id=ordered.order_id,
    )
    return _number_to_dict(number)


@router.get("")
def list_owned_numbers(api_key: ApiKey = Depends(require_api_key)):
    return {"object": "list", "data": [_number_to_dict(n) for n in list_numbers()]}
