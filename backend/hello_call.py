"""Hour 0-1 test: place one real call that just speaks. No ngrok or backend needed.

Usage (PowerShell, inside backend folder with the venv active):
  python hello_call.py +919876543210          # English
  python hello_call.py +919876543210 hi       # Hindi voice test
  python hello_call.py +919876543210 kn       # Kannada voice test
"""
import sys

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

import config
from phrases import LANGS

TEXT = {
    "en": "Hello from CareCall. This is team Carpe Diem. If you can hear this, the call works.",
    "hi": "नमस्ते, यह केयरकॉल है। अगर आप यह सुन पा रहे हैं, तो कॉल ठीक से काम कर रहा है।",
    "kn": "ನಮಸ್ಕಾರ, ಇದು ಕೇರ್‌ಕಾಲ್. ನಿಮಗೆ ಇದು ಕೇಳಿಸುತ್ತಿದ್ದರೆ, ಕರೆ ಸರಿಯಾಗಿ ಕೆಲಸ ಮಾಡುತ್ತಿದೆ.",
}

if len(sys.argv) < 2:
    sys.exit("Usage: python hello_call.py +91XXXXXXXXXX [en|hi|kn]")
to = sys.argv[1]
lang = sys.argv[2] if len(sys.argv) > 2 else "en"

vr = VoiceResponse()
vr.say(TEXT[lang], voice=config.VOICES[lang], language=LANGS[lang]["code"])
vr.pause(length=1)
vr.say(TEXT[lang], voice=config.VOICES[lang], language=LANGS[lang]["code"])

client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
call = client.calls.create(to=to, from_=config.TWILIO_FROM_NUMBER, twiml=str(vr))
print("Call placed. SID:", call.sid)
print("Check: Twilio console > Monitor > Logs > Calls")
