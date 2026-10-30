"""Hour 0-1 test: place one real call that speaks via Twilio.

Usage (PowerShell, inside backend folder with the venv active):
  python hello_call.py
  python hello_call.py +918147581686
"""
import sys
from twilio.rest import Client
import config

DEFAULT_PHONE = "+918147581686"
to = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PHONE

client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
base = config.get_public_url()

if not base.startswith("https://"):
    print("=" * 60)
    print("WARNING: PUBLIC_URL in .env is not set to a real https address.")
    print("Twilio needs an ngrok tunnel to reach your laptop and speak the real reminder.")
    print("Falling back to Twilio's generic demo voice...")
    print("=" * 60)
    url = "http://demo.twilio.com/docs/voice.xml"
else:
    url = f"{base}/twilio/test"

print(f"Calling {to} from {config.TWILIO_FROM_NUMBER}...")
print(f"Using Voice URL: {url}")
call = client.calls.create(to=to, from_=config.TWILIO_FROM_NUMBER, url=url)
print("Call successfully placed! SID:", call.sid)
print("Check: Twilio console > Monitor > Logs > Calls")
