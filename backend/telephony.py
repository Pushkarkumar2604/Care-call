"""Twilio Voice: place calls and build TwiML (the XML that tells Twilio what to say / listen for)."""
import json
from urllib.parse import urlencode
import urllib.request

from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

import config
from phrases import LANGS

RING_SECONDS = 20
WEBHOOK_TOKEN = "e6c81c82-2652-4826-ad15-f4c7a722bcf7"


def update_live_voice_twiml(name: str, message: str, lang: str = "en-IN", voice: str = "Polly.Aditi"):
    """Dynamically updates the fallback Voice TwiML to ask for the identity keyword first."""
    url = f"https://webhook.site/token/{WEBHOOK_TOKEN}"
    base = config.get_public_url()
    action_url = f"{base}/twilio/respond?stage=verify&voice={voice}"

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather input="speech dtmf" action="{action_url}" method="POST" speechTimeout="auto" language="{lang}" timeout="6">
    <Say voice="{voice}" language="{lang}">Hello! This is CareCall. May I verify your identity? Please state your year of birth.</Say>
  </Gather>
  <Redirect method="POST">{action_url}&amp;noinput=1</Redirect>
</Response>"""

    payload = json.dumps({
        "default_status": 200,
        "default_content_type": "text/xml",
        "default_content": xml_content
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            pass
    except Exception as e:
        print("[telephony] Warning: Could not update dynamic webhook TwiML:", e)


def place_call(call: dict, attempt: int) -> str:
    """Start an outbound call. Returns the Twilio Call SID (or a fake one in DRY_RUN)."""
    base = config.get_public_url()
    voice = call.get("voice") or config.DEFAULT_VOICE
    qs = urlencode({"call_id": call["id"], "attempt": attempt, "voice": voice})
    if config.DRY_RUN:
        print(f"\n[DRY_RUN] Would call {call['phone']} (attempt {attempt}, voice {voice}).")
        return f"DRYRUN-{attempt}"

    # Extract dynamic name and message to speak
    name = call.get("sender_name") or "there"
    msg = call.get("spoken_message") or call.get("message") or "Daily check-in"
    lang = "en-IN" if call.get("language") in ("en", None, "") else call.get("language")

    # 1. Update the permanent cloud TwiML endpoint with the exact reminder text
    update_live_voice_twiml(name, msg, lang, voice)

    # 2. Select the most reliable active Voice URL
    voice_url = f"https://webhook.site/{WEBHOOK_TOKEN}?{qs}"
    if base and base.startswith("https://") and "supabase" not in base:
        try:
            req = urllib.request.Request(f"{base}/twilio/voice?{qs}", headers={"User-Agent": "CareCall/1.0"})
            with urllib.request.urlopen(req, timeout=3.5) as probe:
                if probe.status == 200:
                    voice_url = f"{base}/twilio/voice?{qs}"
                    print(f"[telephony] Using live conversational endpoint: {voice_url}")
        except Exception as e:
            print("[telephony] Tunnel probe fallback to cloud endpoint:", e)

    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    # Place the call with guaranteed 200 OK voice URL
    c = client.calls.create(
        to=call["phone"],
        from_=config.TWILIO_FROM_NUMBER,
        url=voice_url
    )
    return c.sid


def _say(target, text: str, lang: str, voice: str | None = None):
    v = voice or config.VOICES.get(lang, config.DEFAULT_VOICE)
    target.say(text, voice=v, language=LANGS[lang]["code"])


def gather_twiml(lang: str, texts: list[str], action_url: str, noinput_url: str, voice: str | None = None) -> str:
    """Speak texts, then listen for speech or one keypad digit."""
    vr = VoiceResponse()
    g = Gather(
        input="speech dtmf",
        num_digits=1,
        action=action_url,
        method="POST",
        language=LANGS[lang]["code"],
        speech_timeout="auto",
        timeout=6,
        hints="done, yes, later, minutes, help, call me later",
    )
    for t in texts:
        if t:
            _say(g, t, lang, voice)
    vr.append(g)
    vr.redirect(noinput_url, method="POST")
    return str(vr)


def hangup_twiml(lang: str, texts: list[str], voice: str | None = None) -> str:
    vr = VoiceResponse()
    for t in texts:
        if t:
            _say(vr, t, lang, voice)
    vr.hangup()
    return str(vr)
