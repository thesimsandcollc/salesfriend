"""A tiny SDK for calling a Switchboard server -- the "simpler than Twilio's
SDK" bet made concrete. No generated code, no per-resource client classes
with dozens of methods; three resources, one HTTP call each.

    from switchboard.client import Switchboard

    sb = Switchboard(api_key="sk_live_...", base_url="http://localhost:8001")
    message = sb.messages.send(from_="+15551234567", to="+15557654321", body="hi")
    call = sb.calls.create(from_="+15551234567", to="+15557654321", say="Hello there")
"""
from __future__ import annotations

import httpx


class SwitchboardError(Exception):
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload
        message = payload.get("error", {}).get("message", str(payload))
        super().__init__(f"[{status_code}] {message}")


class _Resource:
    def __init__(self, client: httpx.Client, path: str):
        self._client = client
        self._path = path

    def _request(self, method: str, path: str = "", **kwargs) -> dict:
        response = self._client.request(method, f"{self._path}{path}", **kwargs)
        if response.status_code >= 400:
            raise SwitchboardError(response.status_code, response.json())
        if response.status_code == 204:
            return {}
        return response.json()


class _Messages(_Resource):
    def send(self, *, from_: str, to: str, body: str) -> dict:
        return self._request("POST", json={"from": from_, "to": to, "body": body})

    def get(self, message_id: str) -> dict:
        return self._request("GET", f"/{message_id}")

    def list(self, *, to: str | None = None, from_: str | None = None, limit: int = 50) -> list[dict]:
        params = {k: v for k, v in {"to": to, "from": from_, "limit": limit}.items() if v is not None}
        return self._request("GET", params=params)["data"]


class _Calls(_Resource):
    def create(self, *, from_: str, to: str, say: str | None = None, forward_to: str | None = None) -> dict:
        return self._request("POST", json={"from": from_, "to": to, "say": say, "forward_to": forward_to})

    def get(self, call_id: str) -> dict:
        return self._request("GET", f"/{call_id}")


class _Numbers(_Resource):
    def available(self, *, area_code: str | None = None, country: str = "US") -> list[dict]:
        params = {"country": country}
        if area_code:
            params["area_code"] = area_code
        return self._request("GET", "/available", params=params)["data"]

    def order(self, phone_number: str) -> dict:
        return self._request("POST", json={"phone_number": phone_number})

    def list(self) -> list[dict]:
        return self._request("GET")["data"]


class _WebhookEndpoints(_Resource):
    def create(self, url: str, event_types: str = "all") -> dict:
        return self._request("POST", json={"url": url, "event_types": event_types})

    def list(self) -> list[dict]:
        return self._request("GET")["data"]

    def delete(self, endpoint_id: str) -> None:
        self._request("DELETE", f"/{endpoint_id}")


class Switchboard:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8001"):
        self._client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=15.0)
        self.messages = _Messages(self._client, "/v1/messages")
        self.calls = _Calls(self._client, "/v1/calls")
        self.numbers = _Numbers(self._client, "/v1/numbers")
        self.webhook_endpoints = _WebhookEndpoints(self._client, "/v1/webhook_endpoints")

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Switchboard":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
