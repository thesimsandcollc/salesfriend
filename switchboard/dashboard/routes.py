"""A minimal operator dashboard: activity feed, API keys, webhook endpoints.

Same login pattern as SalesFriend's dashboard (app/dashboard/routes.py) --
HTTP Basic, open only when no password is set (local dev), required
whenever SWITCHBOARD_DASHBOARD_PASSWORD is set (anything deployed).
"""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from switchboard.config import settings
from switchboard.db import (
    create_api_key,
    create_webhook_endpoint,
    deactivate_webhook_endpoint,
    list_api_keys,
    list_calls,
    list_messages,
    list_numbers,
    list_opt_outs,
    list_recent_deliveries,
    list_webhook_endpoints,
    revoke_api_key,
)
from switchboard.webhooks import run_due_deliveries

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_security = HTTPBasic(auto_error=False)


def _require_login(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    if not settings.dashboard_password:
        return
    valid = credentials is not None and secrets.compare_digest(
        credentials.username, settings.dashboard_username
    ) and secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not valid:
        raise HTTPException(status_code=401, detail="Login required", headers={"WWW-Authenticate": "Basic"})


@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def activity(request: Request):
    return templates.TemplateResponse(
        "activity.html",
        {
            "request": request,
            "messages": list_messages(limit=50),
            "calls": list_calls(limit=50),
            "numbers": list_numbers(),
            "opt_outs": list_opt_outs(),
            "carrier": settings.carrier,
        },
    )


@router.get("/dashboard/api-keys", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def api_keys(request: Request, new_key: str | None = None):
    return templates.TemplateResponse(
        "api_keys.html", {"request": request, "keys": list_api_keys(), "new_key": new_key}
    )


@router.post("/dashboard/api-keys", dependencies=[Depends(_require_login)])
def api_keys_create(label: str = Form(...)):
    _, plaintext = create_api_key(label.strip() or "unlabeled")
    return RedirectResponse(url=f"/dashboard/api-keys?new_key={plaintext}", status_code=303)


@router.post("/dashboard/api-keys/{key_id}/revoke", dependencies=[Depends(_require_login)])
def api_keys_revoke(key_id: int):
    revoke_api_key(key_id)
    return RedirectResponse(url="/dashboard/api-keys", status_code=303)


@router.get("/dashboard/webhooks", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def webhooks_page(request: Request):
    return templates.TemplateResponse(
        "webhook_endpoints.html",
        {
            "request": request,
            "endpoints": list_webhook_endpoints(),
            "deliveries": list_recent_deliveries(limit=30),
        },
    )


@router.post("/dashboard/webhooks", dependencies=[Depends(_require_login)])
def webhooks_create(url: str = Form(...), event_types: str = Form("all")):
    create_webhook_endpoint(url.strip(), event_types.strip() or "all")
    return RedirectResponse(url="/dashboard/webhooks", status_code=303)


@router.post("/dashboard/webhooks/{endpoint_id}/deactivate", dependencies=[Depends(_require_login)])
def webhooks_deactivate(endpoint_id: str):
    deactivate_webhook_endpoint(endpoint_id)
    return RedirectResponse(url="/dashboard/webhooks", status_code=303)


@router.post("/dashboard/webhooks/retry-now", dependencies=[Depends(_require_login)])
def webhooks_retry_now():
    sent = run_due_deliveries()
    return RedirectResponse(url=f"/dashboard/webhooks?retried={sent}", status_code=303)
