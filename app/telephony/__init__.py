from app.telephony.twilio_provider import TwilioProvider

# Swap this line to point at a different RelayProvider implementation
# (see base.py) if you move off Twilio later -- nothing else in the app
# imports Twilio directly.
provider = TwilioProvider()
