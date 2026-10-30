"""Hour 0-1 test: place one real call that speaks via Twilio.

Usage (PowerShell, inside backend folder with the venv active):
  python hello_call.py
  python hello_call.py +918147581686
"""
import sys
import urllib.request
import urllib.error
from twilio.rest import Client
import config

DEFAULT_PHONE = "+918147581686"
to = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PHONE

# Live verified Voice URL delivering valid TwiML
voice_url = config.get_public_url()
if not voice_url or "supabase.co" in voice_url:
    # If the edge function is not yet deployed, fallback to the live verified HTTPS TwiML endpoint
    test_edge_url = "https://ujgqwmhartcreiyuylhb.supabase.co/functions/v1/twilio-voice"
    try:
        with urllib.request.urlopen(test_edge_url, timeout=3) as r:
            if r.status == 200:
                voice_url = test_edge_url
            else:
                voice_url = "https://webhook.site/e6c81c82-2652-4826-ad15-f4c7a722bcf7"
    except Exception:
        voice_url = "https://webhook.site/e6c81c82-2652-4826-ad15-f4c7a722bcf7"

print(f"Checking Voice URL: {voice_url}")

# Pre-flight check: verify HTTP 200 and XML TwiML
try:
    req = urllib.request.Request(voice_url, headers={"User-Agent": "CareCall/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        status = resp.status
        body = resp.read().decode("utf-8").strip()

    if status != 200:
        print(f"ERROR: Voice URL returned HTTP {status}!")
        sys.exit(1)

    if not (body.startswith("<?xml") or body.startswith("<Response")):
        print(f"ERROR: Response does not start with <?xml or <Response:\n{body[:200]}")
        sys.exit(1)

    print("Voice URL check PASSED: Status 200 and valid TwiML received.")
except Exception as e:
    print(f"ERROR: Pre-flight check failed ({e}). Aborting.")
    sys.exit(1)

print(f"Calling contact at {to} from {config.TWILIO_FROM_NUMBER}...")
client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
call = client.calls.create(
    to=to,
    from_=config.TWILIO_FROM_NUMBER,
    url=voice_url
)
print("Call successfully placed! Twilio Call SID:", call.sid)
print("Check: Twilio console > Monitor > Logs > Calls")
