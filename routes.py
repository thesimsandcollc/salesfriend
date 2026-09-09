"""Two read/write dashboards sharing one login:

  /dashboard, /dashboard/{id}          Sales leads from missed calls (unchanged).
  /customers, /customers/new, etc.     The service-reminder customer roster --
                                        this is also where you manually add
                                        customers and where you can trigger an
                                        outreach check on demand.

Both show real phone numbers and conversation transcripts, so once this is
reachable from the open internet (see render.yaml) it needs more than
"nobody knows the URL." See `_require_login` below."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.config import settings
from app.outreach import next_due_at, run_due_outreach
from app.storage.db import (
    CUSTOMER_STATUSES,
    DuplicateCustomerError,
    create_customer,
    get_customer_by_id,
    get_lead_by_id,
    list_customers,
    list_leads,
    update_customer_fields,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_security = HTTPBasic(auto_error=False)


def _require_login(credentials: HTTPBasicCredentials = Depends(_security)) -> None:
    """No DASHBOARD_PASSWORD set -> local/dev mode, dashboard stays open
    (so `uvicorn app.main:app --reload` on your own laptop just works).
    DASHBOARD_PASSWORD set -> every request needs matching HTTP Basic auth.
    Always set it once this is deployed anywhere reachable from outside your
    own machine -- render.yaml requires it for exactly this reason."""
    if not settings.dashboard_password:
        return
    valid = credentials is not None and secrets.compare_digest(
        credentials.username, settings.dashboard_username
    ) and secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Login required",
            headers={"WWW-Authenticate": "Basic"},
        )


@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def dashboard(request: Request):
    leads = list_leads()
    counts = {"new": 0, "texted": 0, "appointment_set": 0, "needs_human": 0}
    for lead in leads:
        counts[lead.status] = counts.get(lead.status, 0) + 1
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "leads": leads, "counts": counts}
    )


@router.get("/dashboard/{lead_id}", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def lead_detail(request: Request, lead_id: int):
    lead = get_lead_by_id(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return templates.TemplateResponse("lead_detail.html", {"request": request, "lead": lead})


# ---------------------------------------------------------------------------
# Customers -- the service-reminder roster.
# ---------------------------------------------------------------------------


def _due_label(customer, now: datetime) -> str:
    due_at = next_due_at(customer, settings.outreach_interval_days)
    if due_at is None:
        return "—"
    days = (due_at.date() - now.date()).days
    if days < 0:
        return f"Due ({-days}d overdue)"
    if days == 0:
        return "Due today"
    return f"in {days}d"


@router.get("/customers", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def customers(request: Request):
    all_customers = list_customers()
    now = datetime.now(timezone.utc)
    counts = {s: 0 for s in CUSTOMER_STATUSES}
    for c in all_customers:
        counts[c.status] = counts.get(c.status, 0) + 1
    rows = [(c, _due_label(c, now)) for c in all_customers]
    return templates.TemplateResponse(
        "customers.html",
        {
            "request": request,
            "rows": rows,
            "counts": counts,
            "interval_days": settings.outreach_interval_days,
        },
    )


@router.post("/customers/run-outreach-now", dependencies=[Depends(_require_login)])
def customers_run_outreach_now():
    sent = run_due_outreach()
    return RedirectResponse(url=f"/customers?sent={len(sent)}", status_code=303)


@router.get("/customers/new", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def customer_new_form(request: Request):
    return templates.TemplateResponse(
        "customer_form.html", {"request": request, "customer": None, "error": None}
    )


@router.post("/customers/new", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def customer_new_submit(
    request: Request,
    phone: str = Form(...),
    name: str = Form(...),
    vehicle_make: str = Form(""),
    vehicle_model: str = Form(""),
    vehicle_year: str = Form(""),
    last_service_date: str = Form(""),
    last_service_type: str = Form(""),
    notes: str = Form(""),
):
    try:
        customer = create_customer(
            phone.strip(),
            name.strip(),
            vehicle_make=vehicle_make.strip() or None,
            vehicle_model=vehicle_model.strip() or None,
            vehicle_year=vehicle_year.strip() or None,
            last_service_date=last_service_date.strip() or None,
            last_service_type=last_service_type.strip() or None,
            notes=notes.strip() or None,
        )
    except DuplicateCustomerError as e:
        return templates.TemplateResponse(
            "customer_form.html",
            {"request": request, "customer": None, "error": str(e)},
            status_code=400,
        )
    return RedirectResponse(url=f"/customers/{customer.id}", status_code=303)


@router.get("/customers/{customer_id}", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def customer_detail(request: Request, customer_id: int):
    customer = get_customer_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse("customer_detail.html", {"request": request, "customer": customer})


@router.get("/customers/{customer_id}/edit", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def customer_edit_form(request: Request, customer_id: int):
    customer = get_customer_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return templates.TemplateResponse(
        "customer_form.html", {"request": request, "customer": customer, "error": None}
    )


@router.post("/customers/{customer_id}/edit", response_class=HTMLResponse, dependencies=[Depends(_require_login)])
def customer_edit_submit(
    request: Request,
    customer_id: int,
    name: str = Form(...),
    vehicle_make: str = Form(""),
    vehicle_model: str = Form(""),
    vehicle_year: str = Form(""),
    last_service_date: str = Form(""),
    last_service_type: str = Form(""),
    notes: str = Form(""),
):
    customer = get_customer_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    update_customer_fields(
        customer.phone,
        name=name.strip(),
        vehicle_make=vehicle_make.strip() or None,
        vehicle_model=vehicle_model.strip() or None,
        vehicle_year=vehicle_year.strip() or None,
        last_service_date=last_service_date.strip() or None,
        last_service_type=last_service_type.strip() or None,
        notes=notes.strip() or None,
    )
    return RedirectResponse(url=f"/customers/{customer_id}", status_code=303)
