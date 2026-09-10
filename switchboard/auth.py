"""API key auth for /v1/*.

Self-hosted, single-tenant: there's no notion of separate customer accounts
here, just bearer tokens that either work or don't. `Authorization: Bearer
sk_live_...` is required on every /v1/* call; the dashboard uses HTTP Basic
instead (see dashboard/routes.py), same split SalesFriend uses.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from switchboard.db import ApiKey, verify_api_key


def require_api_key(authorization: str = Header(default="")) -> ApiKey:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthenticated", "message": "Missing or malformed Authorization header. Expected: Bearer sk_live_..."}},
        )
    token = authorization.removeprefix("Bearer ").strip()
    api_key = verify_api_key(token)
    if api_key is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "unauthenticated", "message": "Invalid or revoked API key."}},
        )
    return api_key
