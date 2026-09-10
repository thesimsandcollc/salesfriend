"""Exercise the full pipeline against a running server with SWITCHBOARD_CARRIER=mock,
without needing a real carrier account -- the mock carrier equivalent of
SalesFriend's simulate_conversation.py.

Usage (server must already be running, e.g. `uvicorn switchboard.main:app --port 8001`):

    python -m switchboard.scripts.simulate send --api-key sk_live_... --from +15550001111 --to +15550002222
    python -m switchboard.scripts.simulate inbound --to +15550001111 --from +15559998888 --body "hi there"
    python -m switchboard.scripts.simulate stop --to +15550001111 --from +15559998888
    python -m switchboard.scripts.simulate call --api-key sk_live_... --from +15550001111 --to +15550002222 --say "Hello!"
    python -m switchboard.scripts.simulate answer --call-control-id <printed by `call`>
"""
from __future__ import annotations

import argparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8001")
    sub = parser.add_subparsers(dest="command", required=True)

    send = sub.add_parser("send", help="Send an outbound message via /v1/messages")
    send.add_argument("--api-key", required=True)
    send.add_argument("--from", dest="from_", required=True)
    send.add_argument("--to", required=True)
    send.add_argument("--body", default="Hello from Switchboard!")

    inbound = sub.add_parser("inbound", help="Simulate an inbound text arriving from a customer")
    inbound.add_argument("--from", dest="from_", required=True)
    inbound.add_argument("--to", required=True)
    inbound.add_argument("--body", default="Sounds good, see you then!")

    stop = sub.add_parser("stop", help="Simulate a customer texting STOP")
    stop.add_argument("--from", dest="from_", required=True)
    stop.add_argument("--to", required=True)

    call = sub.add_parser("call", help="Start an outbound call via /v1/calls")
    call.add_argument("--api-key", required=True)
    call.add_argument("--from", dest="from_", required=True)
    call.add_argument("--to", required=True)
    call.add_argument("--say", default=None)
    call.add_argument("--forward-to", default=None)

    answer = sub.add_parser("answer", help="Simulate the far end answering a call")
    answer.add_argument("--call-control-id", required=True)

    hangup = sub.add_parser("hangup", help="Simulate a call ending")
    hangup.add_argument("--call-control-id", required=True)
    hangup.add_argument("--status", default="completed")

    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=15.0) as client:
        if args.command == "send":
            r = client.post("/v1/messages", headers={"Authorization": f"Bearer {args.api_key}"},
                             json={"from": args.from_, "to": args.to, "body": args.body})
        elif args.command == "inbound":
            r = client.post("/carrier/mock/inbound-message", json={"from": args.from_, "to": args.to, "body": args.body})
        elif args.command == "stop":
            r = client.post("/carrier/mock/inbound-message", json={"from": args.from_, "to": args.to, "body": "STOP"})
        elif args.command == "call":
            r = client.post("/v1/calls", headers={"Authorization": f"Bearer {args.api_key}"},
                             json={"from": args.from_, "to": args.to, "say": args.say, "forward_to": args.forward_to})
        elif args.command == "answer":
            r = client.post("/carrier/mock/call-event", json={"carrier_call_id": args.call_control_id, "event": "answered"})
        elif args.command == "hangup":
            r = client.post("/carrier/mock/call-event", json={"carrier_call_id": args.call_control_id, "event": "hangup", "call_status": args.status})
        else:
            raise SystemExit(f"Unknown command: {args.command}")

    print(r.status_code, r.json())


if __name__ == "__main__":
    main()
