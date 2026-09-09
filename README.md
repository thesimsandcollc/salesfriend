# SalesFriend (prototype)

An agentic texting assistant for a dealership, built around two pipelines
that share the same phone number, AI engine, and dashboard:

1. **Missed-call catch.** A call to the business line goes unanswered ->
   auto-text the caller -> AI conversation finds out what vehicle they want
   and when they'd like to come in -> logged to a lead list.
2. **Proactive service reminders.** You enter your existing customers (name,
   phone, vehicle, last service date) -> on a recurring schedule, SalesFriend
   texts each one who's due for an oil change / tire rotation / etc. -> AI
   conversation gets them scheduled -> logged to a customer list.

This is the working-prototype stage described in the SalesFriend plan doc —
built to be tried privately first, not switched on for real customers yet.

## How it fits together

```
                    MISSED-CALL PIPELINE
Customer calls business number
        |
        v
  Twilio rings the salesperson's real cell  ---(answered)---> normal call, nothing else happens
        |
   (no answer / busy / times out)
        v
  Auto-text sent to the customer
        |
        v
  AI conversation asks about the vehicle, trade-in, and an
  appointment time -- logged as a lead on /dashboard


                 SERVICE-REMINDER PIPELINE
You add a customer on /customers (name, phone, vehicle, last service date)
        |
        v
  Background check (hourly by default) -- is this customer due?
        |
   (due: OUTREACH_INTERVAL_DAYS since last contact/service)
        v
  Auto-text sent to the customer
        |
        v
  AI conversation offers to schedule service -- logged as a
  customer record on /customers


                        SHARED
  Every reply (either pipeline) -> POST /sms/incoming -> STOP is checked
  first, then routed to whichever list that phone number is on
        |
        v
  appointment set / needs human -> salesperson gets a text
```

Twilio (or another relay -- see `app/telephony/base.py`) is the only piece
that touches real phone numbers. Everything else -- the conversation logic,
the customer/lead lists, the dashboard -- is this application, so switching
relay providers later only means writing one new file, not rebuilding the
product.

## What's here

```
app/
  main.py                     FastAPI app -- Twilio webhooks + startup + the
                               outreach background task
  config.py                   All settings, read from environment variables
  compliance.py                STOP/opt-out keyword detection (shared by both pipelines)
  outreach.py                  Due-date logic + the recurring background loop
                               for the service-reminder pipeline
  telephony/
    base.py                   The RelayProvider interface (the "swappable" part)
    twilio_provider.py        Twilio implementation -- the only file that imports Twilio
  agent/
    conversation.py           AI conversation logic for the missed-call/lead pipeline
    customer_conversation.py  AI conversation logic for the service-reminder pipeline
  storage/
    db.py                     SQLite: leads+messages, and customers+customer_messages
  dashboard/
    routes.py, templates/     Leads list, customer roster + add/edit forms, transcripts
scripts/
  init_db.py                          Creates the SQLite file
  simulate_conversation.py            Try the lead conversation in your terminal
  simulate_customer_conversation.py   Try the service-reminder conversation in your terminal
```

## 1. Try the conversation logic first (no Twilio, no phone needed)

This is the fastest way to see whether the agent's questions and
appointment-setting actually feel right, before touching real phone numbers.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

To try it **without any API key**, leave `.env` as-is (`MOCK_AGENT=true`) and run:

```bash
python -m scripts.simulate_conversation
```

To try the **real AI conversation**, put a real key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
MOCK_AGENT=false
```

(Check https://docs.claude.com/en/docs/about-claude/models for the current
model name if `ANTHROPIC_MODEL` in `.env.example` is out of date by the time
you read this.) Then run the same command and have a conversation as if you
were the lead who just called.

For the **service-reminder side**, there's a second simulator:

```bash
python -m scripts.simulate_customer_conversation
```

This creates a test customer whose last service date is set far enough in
the past to be immediately "due," sends the same initial text the automatic
background job would send, and lets you reply as that customer.

Every conversation you run this way is saved to `salesfriend.db` and shows
up on the dashboard (`uvicorn app.main:app --reload`, then visit
`http://localhost:8000/dashboard` for leads or `/customers` for the service
roster).

## 2. Wire up real phones (Twilio)

You'll need:

1. A [Twilio account](https://www.twilio.com) with a phone number purchased
   (Phone Numbers → Buy a Number). This becomes the business number
   customers call/text.
2. `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER` from
   the Twilio console, in `.env`.
3. `SALESPERSON_PHONE_NUMBER` set to the real cell that should actually ring.
4. A way for Twilio to reach this app over the internet. For local testing,
   [ngrok](https://ngrok.com) works well:
   ```bash
   ngrok http 8000
   ```
   Put the `https://...ngrok-free.app` URL it gives you into `.env` as
   `PUBLIC_BASE_URL` (no trailing slash).
5. In the Twilio console, on your phone number's configuration page, set:
   - **A call comes in** → Webhook → `PUBLIC_BASE_URL/voice/incoming` (HTTP POST)
   - **A message comes in** → Webhook → `PUBLIC_BASE_URL/sms/incoming` (HTTP POST)

Then run the app:

```bash
python -m scripts.init_db
uvicorn app.main:app --reload
```

Call the Twilio number from any other phone. If you don't answer within
`RING_TIMEOUT_SECONDS`, you (the caller) should get a text within a few
seconds, and the conversation continues from there. Check `/dashboard` to
watch the lead show up.

**Test this on your own numbers first.** Don't point real customers at it
until you've run through a few full conversations yourself and are happy
with the tone and the questions it asks.

## 3. Add your customer list (service reminders)

This pipeline doesn't wait for anyone to call -- it works entirely off what
you enter on `/customers`:

1. Go to `/customers/new` and add a customer: name and phone are required;
   vehicle make/model/year and last service date are optional but strongly
   recommended (see the warning on that form -- without a last-service date,
   a newly added customer won't get a first text until a full
   `OUTREACH_INTERVAL_DAYS` has passed from *today*, even if they're
   actually overdue right now).
2. Leave it running. Once an hour (`OUTREACH_CHECK_INTERVAL_SECONDS`), the
   app checks every customer and texts anyone due -- by default, 180 days
   since their last service or last contact, whichever is more recent.
3. To test without waiting, click **Run outreach check now** on `/customers`
   -- it runs that same check immediately and tells you how many texts went
   out.
4. After a customer actually comes in for service, open their record and hit
   **Edit** to update their last service date -- that resets their clock for
   the next reminder.

There's no bulk import yet -- customers are added one at a time through that
form. For an existing client list of any real size, ask for a CSV-import
feature to be added before doing this by hand for hundreds of people.

**This is the pipeline that needs the most care before going live.** Texting
someone who just called you is one thing; texting your whole customer list
on a schedule is exactly the pattern SMS carriers and regulations watch for.
Two concrete things, beyond the general consent note below:

- **STOP is handled**, but registration isn't automatic. In the US, sending
  this kind of proactive application-to-person texting through Twilio
  requires **A2P 10DLC registration** (a Brand + Campaign in Twilio's
  console) -- without it, carriers throttle or block the traffic outright,
  and Twilio may charge more for unregistered messages. This is a real step
  with its own review time and fees; see [Twilio's A2P 10DLC
  docs](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc) and do
  this before turning the automatic loop on for real customers. The
  missed-call pipeline technically falls under the same requirement, but
  carriers scrutinize a scheduled, proactive campaign (this pipeline) more
  than a one-off reply to an inbound call.
- **Every outreach text includes "Reply STOP to opt out"** in the message
  itself (see `app/outreach.py`'s `build_initial_message`) -- don't remove
  that if you customize the wording.

## 4. Put it somewhere that's always on (Render)

Everything above runs on your own laptop with an ngrok tunnel, which is
great for testing but goes offline the moment you close the lid. `render.yaml`
in this repo is a ready-to-use [Render](https://render.com) Blueprint that
deploys this app as an always-on service with persistent storage for both
the lead and customer databases, so records survive restarts and redeploys.

This matters even more now than it did for the missed-call pipeline alone:
the service-reminder cycle is a background loop running *inside* this app
(see `app/outreach.py`). If the app isn't running, or is spun down, no
reminders go out -- there's no separate cron job keeping it alive.

Steps:

1. Push this project to a GitHub repo (Render deploys from git).
2. In the Render dashboard: **New → Blueprint**, point it at that repo.
   Render reads `render.yaml` and shows you the service it's about to
   create, including a persistent disk for `salesfriend.db`.
3. You'll be prompted to fill in the secret values (`ANTHROPIC_API_KEY`,
   the `TWILIO_*` values, `SALESPERSON_PHONE_NUMBER`) — these are never
   written into the repo.
4. Deploy. Render assigns a URL like `https://salesfriend-xyz.onrender.com`.
   Copy it, then edit the `PUBLIC_BASE_URL` environment variable on the
   service to match (Environment tab → edit → save, which redeploys).
5. Update your Twilio phone number's webhooks (same two settings as step 2
   above) to point at that Render URL instead of the ngrok one.

A couple of things worth knowing before you do this:

- **Skip Render's free tier for this app specifically.** A free web service
  spins down after 15 minutes of no traffic and takes 30-60 seconds to wake
  back up — long enough that Twilio's webhook can time out and a missed
  call's auto-text just... doesn't send. `render.yaml` is set to Render's
  smallest paid tier (currently named `0.5c-512mb`, roughly $7/mo) for this
  reason. Free tier also doesn't support the persistent disk the lead
  database needs.
- Render's plan names and prices are theirs to change — check the current
  ones on your dashboard before deploying; `render.yaml` may need a small
  edit if they've renamed things since this was written.
- All-in, hosting adds roughly $7-8/month on top of the Twilio/Claude usage
  costs in the plan doc.

## Costs (see the plan doc for the full breakdown)

Roughly $10-25/month at one salesperson's volume for the missed-call
pipeline alone: a Twilio number (~$1-2/mo), SMS/voice usage (fractions of a
cent each), light hosting, and light Claude API usage. The service-reminder
pipeline adds to that in proportion to how many customers you load in and
how often they text back -- still cents per conversation, but worth watching
once your list is in the hundreds. Nothing here has a fixed monthly platform
fee beyond that, aside from Twilio's A2P 10DLC registration fee (see above).

## Before this touches real customers

- **Texting consent.** Replying to someone who just called you is one
  thing; texting your whole customer list on a schedule is another --
  see the A2P 10DLC note above, which is not optional at any real volume.
  Keep the first message tied to something concrete (their last visit),
  make opting out easy (STOP is already wired up), and don't repurpose
  either list for unrelated marketing without separate consent. This isn't
  legal advice — worth a quick check against the dealership's own
  compliance process either way.
- **Read every transcript for a while.** The mock mode and a live test run
  are not the same as a real, sometimes-annoyed customer. Keep an eye on
  `/dashboard` and `/customers` daily at first.
- **`needs_human` is a safety valve, not an edge case.** Anything about
  price negotiation, financing approval, or a complaint should route to the
  salesperson, not get a bot's best guess -- true in both pipelines.
- **Load a small batch of real customers before the whole list.** Add five
  or ten real customers first, watch how the conversations actually go for
  a cycle, then load the rest.

## Known limitations of this prototype

- No calendar sync yet (by design, for now — see the plan doc). Appointments
  live only in these lists; someone has to carry them over to a real
  calendar.
- Only Twilio is implemented as a relay provider. `app/telephony/base.py` is
  the interface to implement for a different one.
- Single salesperson, single dealership. Scaling to more people means adding
  a notion of "which salesperson owns this lead/customer" — the plan doc's
  rollout order gets you here after this stage is validated.
- No bulk/CSV import for customers -- one at a time via `/customers/new`.
- No re-opt-in flow: once a phone number is marked `opted_out`, nothing in
  the UI reverses that (by design -- consent should be sticky), so fixing a
  mistaken opt-out means editing the database directly for now.
- The Render deployment (`render.yaml`) has been checked against Render's
  current documented Blueprint format, but not run against a live Render
  account — Render's plan names and blueprint fields do change over time, so
  treat the first deploy as something to watch closely, not a sure thing.
