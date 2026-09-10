# Switchboard

A self-hosted, developer-first alternative to Twilio's core Programmable
Messaging + Voice products. You run this (on your own laptop, or on a $7/mo
box), it wraps a cheaper carrier underneath, and your app talks to a small
JSON API instead of Twilio's.

**What "better than Twilio" means here, concretely:**

| | Twilio | Switchboard |
|---|---|---|
| Carrier | Twilio itself | [Telnyx](https://telnyx.com) by default -- typically 20-40% cheaper per SMS/minute, swappable (see `carriers/`) |
| Voice control | TwiML (XML markup) | Two JSON fields (`say`, `forward_to`) on the same request that starts the call |
| Opt-out (STOP/HELP/START) | A setting you have to know to turn on (Advanced Opt-Out) | Always on, for every number, no configuration |
| Webhook delivery | A separate "Debugger" product | A table in your own database (`webhook_deliveries`), retried automatically, inspectable in `/dashboard/webhooks` |
| Ownership | Twilio's platform, Twilio's roadmap | Your database, your server, MIT-yours-to-change code |

**What this is *not*:** a replacement for a real carrier. No app, however
well written, can originate SMS/calls on the actual telephone network by
itself -- someone has to be a carrier or resell one. Switchboard is the
"better Twilio" *layer*, wrapping Telnyx (or, in `mock` mode, nothing at
all) the same way SalesFriend's `TwilioProvider` wraps Twilio today.

## Quickstart (no carrier account needed)

```bash
cd salesfriend   # repo root -- Switchboard's imports assume this as cwd
python -m venv .venv && source .venv/bin/activate
pip install -r switchboard/requirements.txt
cp switchboard/.env.example switchboard/.env
python -m switchboard.scripts.init_db
uvicorn switchboard.main:app --reload --port 8001
```

`SWITCHBOARD_CARRIER=mock` is the default -- the server starts, mints and
prints a bootstrap API key on first run (also visible any time at
`/dashboard/api-keys`), and every endpoint below works with zero external
accounts. Nothing actually reaches a real phone; see "Simulating traffic"
below for how to exercise the full send/receive/webhook pipeline anyway.

Visit `http://localhost:8001/dashboard` for the activity feed, API keys, and
webhook endpoints.

## The API

Every `/v1/*` call needs `Authorization: Bearer sk_live_...`. Errors are
`{"error": {"code": "...", "message": "..."}}` with a matching HTTP status.

### Send a message

```bash
curl -X POST http://localhost:8001/v1/messages \
  -H "Authorization: Bearer $SWITCHBOARD_API_KEY" -H "Content-Type: application/json" \
  -d '{"from": "+15550001111", "to": "+15559998888", "body": "Hi there!"}'
```

Rejects with `422 recipient_opted_out` if `to` has ever texted STOP --
enforced by the platform, not left to your app to remember.

`GET /v1/messages/{id}`, `GET /v1/messages?to=...&from=...` -- fetch/list.

### Start a call

```bash
curl -X POST http://localhost:8001/v1/calls \
  -H "Authorization: Bearer $SWITCHBOARD_API_KEY" -H "Content-Type: application/json" \
  -d '{"from": "+15550001111", "to": "+15559998888", "say": "Thanks for calling, we will text you shortly."}'
```

`say` plays text-to-speech once answered; `forward_to` bridges the call to
another number instead (or both -- speak, then transfer). No TwiML, no
separate call-flow document.

### Numbers

`GET /v1/numbers/available?area_code=415`, `POST /v1/numbers {"phone_number": "+1415..."}`,
`GET /v1/numbers`.

### Webhook endpoints -- getting events into your own app

```bash
curl -X POST http://localhost:8001/v1/webhook_endpoints \
  -H "Authorization: Bearer $SWITCHBOARD_API_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://your-app.example.com/webhooks/switchboard"}'
```

Returns a `signing_secret` (shown again any time via GET or the dashboard --
it's not an access credential, just what lets your app verify a delivery is
real). Every event type: `message.received`, `message.status_updated`,
`message.opted_out`, `message.opted_in`, `call.status_updated`.

Each delivery arrives as `POST <your url>` with:
```
Switchboard-Signature: t=<unix ts>,v1=<hex hmac-sha256>
Switchboard-Event: message.received
Switchboard-Delivery: WD...
```
Verify it (Python):
```python
import hmac, hashlib
expected = hmac.new(secret.encode(), f"{timestamp}.{raw_body}".encode(), hashlib.sha256).hexdigest()
hmac.compare_digest(expected, received_v1)
```
Failed deliveries retry on a schedule (30s, 2m, 10m, 30m, 1h) for up to
`SWITCHBOARD_WEBHOOK_MAX_ATTEMPTS` attempts, visible in `/dashboard/webhooks`.

### Python client

```python
from switchboard.client import Switchboard

sb = Switchboard(api_key="sk_live_...", base_url="http://localhost:8001")
sb.messages.send(from_="+15550001111", to="+15559998888", body="hi")
```

## Simulating traffic (mock carrier)

```bash
export KEY=<the bootstrap key printed on startup, or one from /dashboard/api-keys>

# Send outbound
python -m switchboard.scripts.simulate send --api-key $KEY --from +15550001111 --to +15559998888

# Simulate the customer texting back -- runs through compliance + your webhooks
python -m switchboard.scripts.simulate inbound --from +15559998888 --to +15550001111 --body "Sounds good!"
python -m switchboard.scripts.simulate stop --from +15559998888 --to +15550001111

# Simulate a call being answered, then ending
python -m switchboard.scripts.simulate call --api-key $KEY --from +15550001111 --to +15559998888 --say "Hello!"
python -m switchboard.scripts.simulate answer --call-control-id <printed above>
python -m switchboard.scripts.simulate hangup --call-control-id <same id>
```

## Going live (Telnyx)

1. Create a [Telnyx account](https://telnyx.com), buy a number (Portal ->
   Numbers), create a Messaging Profile and a Call Control Connection, and
   assign the number to both.
2. Fill in `TELNYX_API_KEY`, `TELNYX_PUBLIC_KEY` (for webhook signature
   verification), `TELNYX_MESSAGING_PROFILE_ID`, `TELNYX_CONNECTION_ID` in
   `.env`, and set `SWITCHBOARD_CARRIER=telnyx`.
3. Set `SWITCHBOARD_PUBLIC_BASE_URL` to a URL Telnyx can reach (ngrok for
   local testing, same as SalesFriend's own setup).
4. In the Telnyx portal, point the Messaging Profile's inbound webhook at
   `PUBLIC_BASE_URL/carrier/telnyx/inbound-message` and the delivery-status
   webhook at `PUBLIC_BASE_URL/carrier/telnyx/message-status`; point the
   Call Control Connection's webhook at `PUBLIC_BASE_URL/carrier/telnyx/call-event`.
5. Set `SWITCHBOARD_DASHBOARD_PASSWORD` before deploying anywhere public.

**A2P 10DLC / compliance still applies.** Switchboard enforces STOP/HELP/
START mechanics automatically, but it can't register your traffic with US
carriers for you -- that's still a real step with Telnyx (or any carrier)
before sending proactive/marketing SMS at volume. See
[Telnyx's 10DLC docs](https://support.telnyx.com/en/articles/6415113-10dlc-overview).

## Deploying

`render.yaml` in this directory is a Render Blueprint -- see its comments
for the Root Directory gotcha (leave it at the repo root, not `switchboard/`).

## Architecture

```
switchboard/
  main.py                 FastAPI app: mounts routers, startup, webhook retry loop
  config.py                Settings from environment variables
  db.py                     SQLite: api_keys, numbers, messages, calls, opt_outs,
                             webhook_endpoints, webhook_deliveries -- no ORM
  auth.py                   API key verification (Authorization: Bearer ...)
  compliance.py             STOP/HELP/START keyword handling
  webhooks.py               Signs + delivers events to registered endpoints, with retry
  carriers/
    base.py                 CarrierProvider interface -- the only thing routes depend on
    mock_provider.py         Zero-credential dev/test carrier
    telnyx_provider.py       Real carrier: Telnyx Messaging + Call Control APIs
  api/
    messages.py, calls.py, numbers.py, webhook_endpoints.py   The /v1/* routes
  inbound.py                Where the carrier posts events back to us (/carrier/{carrier}/*)
  dashboard/                Activity feed, API keys, webhook endpoints (HTTP Basic)
  client/python_client.py   Minimal Python SDK
  scripts/                  init_db, create_api_key, simulate (mock-carrier testing)
```

Swapping carriers later (Bandwidth, Plivo, Vonage, ...) means implementing
`carriers/base.py`'s `CarrierProvider` once and adding one branch in
`carriers/__init__.py` -- nothing in `api/`, `inbound.py`, or the dashboard
imports a carrier SDK directly. Same idea as SalesFriend's own
`app/telephony/base.py`, one layer further out.

## Known limitations of this prototype

- Single-tenant: one set of API keys and one number pool per deployed
  instance, no concept of separate customer "accounts" or billing. Good fit
  for "your own app(s) calling your own Switchboard"; not a multi-tenant SaaS
  as-is.
- Voice support covers the two things SalesFriend-style apps actually need
  (speak a message, forward/bridge a call) -- not a full IVR/flow builder
  like Twilio Studio or TwiML's `<Gather>`/`<Record>` verbs.
- No usage-based billing/metering -- you pay Telnyx directly for what you use.
- The Telnyx integration is written against Telnyx's documented API
  contract but hasn't been run against a live Telnyx account in this
  session -- treat the first real send/call as something to watch closely.
- `SWITCHBOARD_WEBHOOK_MAX_ATTEMPTS` retries are timer-based via an asyncio
  loop inside this process; if the process restarts mid-backoff, a delivery
  due in the near future just waits for the next check instead of firing
  exactly on schedule -- fine for this scale, worth knowing.
