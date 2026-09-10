"""Central place every Switchboard setting is read from.

Same pattern as the SalesFriend app this lives alongside: everything comes
from environment variables (a .env file in local dev), nothing secret lives
in code.
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
    # Which CarrierProvider backs this instance. "mock" needs no credentials
    # and is the default so the API is usable with zero setup; "telnyx" is
    # the real carrier integration.
    carrier: str

    telnyx_api_key: str
    telnyx_public_key: str          # Ed25519 public key, for verifying inbound webhook signatures
    telnyx_messaging_profile_id: str
    telnyx_connection_id: str        # Call Control connection, for voice

    database_path: str
    public_base_url: str

    webhook_retry_max_attempts: int
    webhook_retry_check_interval_seconds: int

    dashboard_username: str
    dashboard_password: str

    # First API key to create automatically on a totally fresh database, so
    # `python -m switchboard.scripts.init_db` leaves you with something to
    # actually call the API with. Printed once, never stored in plaintext.
    bootstrap_api_key_label: str


def load_settings() -> Settings:
    return Settings(
        carrier=os.getenv("SWITCHBOARD_CARRIER", "mock").strip().lower(),
        telnyx_api_key=os.getenv("TELNYX_API_KEY", ""),
        telnyx_public_key=os.getenv("TELNYX_PUBLIC_KEY", ""),
        telnyx_messaging_profile_id=os.getenv("TELNYX_MESSAGING_PROFILE_ID", ""),
        telnyx_connection_id=os.getenv("TELNYX_CONNECTION_ID", ""),
        database_path=os.getenv("SWITCHBOARD_DATABASE_PATH", "./switchboard.db"),
        public_base_url=os.getenv("SWITCHBOARD_PUBLIC_BASE_URL", "").rstrip("/"),
        webhook_retry_max_attempts=int(os.getenv("SWITCHBOARD_WEBHOOK_MAX_ATTEMPTS", "6")),
        webhook_retry_check_interval_seconds=int(
            os.getenv("SWITCHBOARD_WEBHOOK_RETRY_INTERVAL_SECONDS", "60")
        ),
        dashboard_username=os.getenv("SWITCHBOARD_DASHBOARD_USERNAME", "admin"),
        dashboard_password=os.getenv("SWITCHBOARD_DASHBOARD_PASSWORD", ""),
        bootstrap_api_key_label=os.getenv("SWITCHBOARD_BOOTSTRAP_KEY_LABEL", "bootstrap"),
    )


settings = load_settings()
