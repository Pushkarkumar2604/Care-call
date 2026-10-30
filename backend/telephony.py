"""Twilio Voice: place calls and build TwiML (the XML that tells Twilio what to say / listen for)."""
from urllib.parse import urlencode

from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

import config
from phrases import LANGS

RING_SECONDS = 20  # how long the phone rings before Twilio gives up ("no-answer")


def place_call(call: dict, attempt: int) -> str:
    """Start an outbound call. Returns the Twilio Call SID (or a fake one in DRY_RUN)."""
    base = config.get_public_url()
    qs = urlencode({"call_id": call["id"], "attempt": attempt})
    if config.DRY_RUN:
        print(f"\n[DRY_RUN] Would call {call['phone']} (attempt {attempt}).")
        print(f"[DRY_RUN] Simulate it:  python simulate_call.py {call['id']} \"yes I took it\"\n")
        return f"DRYRUN-{attempt}"
    if not base.startswith("https://"):
        raise RuntimeError("PUBLIC_URL is not set to the https ngrok address")
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    c = client.calls.create(
        to=call["phone"],
        from_=config.TWILIO_FROM_NUMBER,
        url=f"{base}/twilio/voice?{qs}",
        method="POST",
        status_callback=f"{base}/twilio/status?{qs}",
        status_callback_event=["completed"],
        status_callback_method="POST",
        timeout=RING_SECONDS,
    )
    return c.sid


def _say(target, text: str, lang: str):
    target.say(text, voice=config.VOICES.get(lang, config.VOICES["en"]), language=LANGS[lang]["code"])


def gather_twiml(lang: str, texts: list[str], action_url: str, noinput_url: str) -> str:
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
            _say(g, t, lang)
    vr.append(g)
    vr.redirect(noinput_url, method="POST")  # reached only if the person says nothing
    return str(vr)


def hangup_twiml(lang: str, texts: list[str]) -> str:
    vr = VoiceResponse()
    for t in texts:
        if t:
            _say(vr, t, lang)
    vr.hangup()
    return str(vr)
