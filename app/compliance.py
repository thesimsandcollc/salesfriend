"""Shared opt-out handling for any outbound texting pipeline in this app.

This matters far more for the proactive customer-outreach pipeline than the
missed-call one -- texting someone who just called you is one thing; texting
your whole customer list on a schedule is the pattern SMS regulations (and
carriers) actually watch for. See the plan doc and README for the bigger
picture (consent, and the A2P 10DLC registration this kind of traffic needs
in the US) -- this module is just the "never text someone who said stop"
mechanics.
"""
from __future__ import annotations

# The standard CTIA/carrier-recognized opt-out keywords. Twilio's own
# Advanced Opt-Out feature may already intercept some of these before they
# reach this app at all, depending on how the number/messaging service is
# configured -- this is a second, explicit safety net, not a substitute for
# getting that Twilio-side configuration and A2P registration right.
OPT_OUT_KEYWORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}


def is_opt_out(body: str) -> bool:
    return body.strip().strip(".!").lower() in OPT_OUT_KEYWORDS
