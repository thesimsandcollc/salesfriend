"""Central place every setting is read from.

Everything here comes from environment variables (loaded from a .env file in
local dev) so nothing secret ever lives in code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_model: str

    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str

    salesperson_phone_number: str
    salesperson_name: str
    dealership_name: str

    ring_timeout_seconds: int
    notify_salesperson: bool

    outreach_interval_days: int
    outreach_check_interval_seconds: int

    database_path: str
    public_base_url: str

    mock_agent: bool

    dashboard_username: str
    dashboard_password: str


def load_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        twilio_account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
        twilio_phone_number=os.getenv("TWILIO_PHONE_NUMBER", ""),
        salesperson_phone_number=os.getenv("SALESPERSON_PHONE_NUMBER", ""),
        salesperson_name=os.getenv("SALESPERSON_NAME", "the salesperson"),
        dealership_name=os.getenv("DEALERSHIP_NAME", "the dealership"),
        ring_timeout_seconds=int(os.getenv("RING_TIMEOUT_SECONDS", "25")),
        notify_salesperson=_bool("NOTIFY_SALESPERSON", True),
        outreach_interval_days=int(os.getenv("OUTREACH_INTERVAL_DAYS", "180")),
        outreach_check_interval_seconds=int(os.getenv("OUTREACH_CHECK_INTERVAL_SECONDS", "3600")),
        database_path=os.getenv("DATABASE_PATH", "./salesfriend.db"),
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        mock_agent=_bool("MOCK_AGENT", False),
        dashboard_username=os.getenv("DASHBOARD_USERNAME", "admin"),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
    )


settings = load_settings()
