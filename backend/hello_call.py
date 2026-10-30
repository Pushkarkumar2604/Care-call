"""Hour 0-1 test: place one real call that speaks via Twilio.

Usage (PowerShell, inside backend folder with the venv active):
  python hello_call.py
  python hello_call.py +918147581686
"""
import sys
from twilio.rest import Client
import config

# Default to your verified phone number if no argument is provided
DEFAULT_PHONE = "+918147581686"
to = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PHONE

print(f"Calling {to} from {config.TWILIO_FROM_NUMBER}...")

client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
# demo.twilio.com/docs/voice.xml is Twilio's official hosted XML test URL
call = client.calls.create(
    to=to,
    from_=config.TWILIO_FROM_NUMBER,
    url="http://demo.twilio.com/docs/voice.xml"
)
print("Call successfully placed! SID:", call.sid)
print("Check: Twilio console > Monitor > Logs > Calls")
