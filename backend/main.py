"""CareCall backend: FastAPI app.

Run:  uvicorn main:app --port 8000
  /api/...     used by the v0 dashboard (or the fallback page at /)
  /twilio/...  webhooks Twilio calls during a phone call (through ngrok)
"""
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import config
import scheduler
from agents import clamp_minutes, guardian_agent, listener_agent, planner_agent, reporter_agent
from db import BACKEND_NAME, db, now_iso
from phrases import LANGS, lang_of, p
from telephony import gather_twiml, hangup_twiml
from twilio.twiml.voice_response import VoiceResponse

MAX_QUESTIONS = 2  # questions answered per call
MAX_UNCLEAR = 1    # re-asks after an unclear reply before giving up on this attempt


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    print(f"[carecall] DRY_RUN={config.DRY_RUN} DEMO_MODE={config.DEMO_MODE} DB={BACKEND_NAME} "
          f"LLM={config.LLM_PROVIDER} PUBLIC_URL={config.get_public_url() or '(not set)'}")
    yield
    scheduler.shutdown()


app = FastAPI(title="CareCall", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def xml(twiml: str) -> Response:
    return Response(content=twiml, media_type="application/xml")


# ==========================================================================
# Dashboard API
# ==========================================================================
class CallIn(BaseModel):
    phone: str
    message: str
    language: str = "en"
    scheduled_at: str            # ISO time, e.g. 2026-11-01T10:30:00.000Z
    note: str = ""               # extra facts the AI may use to answer questions
    sender_name: str = ""


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.api_route("/twilio/test", methods=["GET", "POST"])
def twilio_test():
    """Test voice call endpoint: speaks the CareCall intro and medicine reminder."""
    vr = VoiceResponse()
    vr.say("Hello from CareCall. This is team Carpe Diem.", language="en-IN")
    vr.pause(length=1)
    vr.say("Please remember to take your blood pressure tablet after breakfast. If you can hear this, your CareCall reminder system is working successfully!", language="en-IN")
    return Response(content=str(vr), media_type="application/xml")


@app.get("/api/health")
def health():
    return {"ok": True, "dry_run": config.DRY_RUN, "demo_mode": config.DEMO_MODE, "db": BACKEND_NAME,
            "llm": config.LLM_PROVIDER, "public_url": config.get_public_url(),
            "languages": config.ENABLED_LANGUAGES, "jobs": len(scheduler.sched.get_jobs())}


@app.post("/api/calls")
def create_call(body: CallIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise HTTPException(400, "Phone must be in international format, e.g. +919876543210")
    if not body.message.strip():
        raise HTTPException(400, "Message is empty")
    language = body.language if body.language in LANGS else "en"
    try:
        when = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "scheduled_at must be an ISO date-time")
    if when.tzinfo is None:
        when = when.astimezone()  # treat as this laptop's local time
    when = max(when, scheduler.utcnow() + timedelta(seconds=5))

    plan = planner_agent(body.message.strip(), language)
    call_id = str(uuid.uuid4())
    row = {
        "id": call_id,
        "phone": phone,
        "message": body.message.strip(),
        "note": body.note.strip(),
        "sender_name": body.sender_name.strip(),
        "language": language,
        "spoken_message": plan["spoken_message"],
        "priority": plan["priority"],
        "priority_reason": plan["reason"],
        "max_attempts": plan["max_attempts"],
        "retry_gap_seconds": plan["retry_gap_seconds"],
        "attempts": 0,
        "failed_attempts": 0,
        "status": "scheduled",
        "scheduled_at": when.isoformat(),
        "next_attempt_at": when.isoformat(),
        "summary": "",
        "twilio_sid": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    db.insert_call(row)
    db.add_event(call_id, "PLANNER",
                 f"Priority {plan['priority'].upper()} ({plan['reason']}). Plan: up to {plan['max_attempts']} "
                 f"attempts, {plan['retry_gap_seconds']} s apart. [{plan['source']}]")
    if lang_of(row) != language:
        db.add_event(call_id, "SYSTEM", f"{LANGS[language]['name']} is disabled; this call will use English.")
    scheduler.schedule_attempt(call_id, when)
    db.add_event(call_id, "SCHEDULER", f"Queued for {when.astimezone():%d %b %H:%M:%S}.")
    return db.get_call(call_id)


@app.get("/api/calls")
def list_calls():
    calls = db.list_calls()
    events = db.list_events([c["id"] for c in calls])
    by_call = {}
    for e in events:
        by_call.setdefault(e["call_id"], []).append(e)
    for c in calls:
        c["events"] = by_call.get(c["id"], [])
    return calls


@app.post("/api/calls/{call_id}/call-now")
def call_now(call_id: str):
    c = db.get_call(call_id)
    if not c:
        raise HTTPException(404, "Not found")
    if c["status"] not in ("scheduled", "rescheduled", "retrying"):
        db.update_call(call_id, status="scheduled", failed_attempts=0)
    scheduler.schedule_attempt(call_id, scheduler.utcnow())
    db.add_event(call_id, "SCHEDULER", "Manual 'call now'.")
    return {"ok": True}


@app.delete("/api/calls")
def reset_all():
    scheduler.cancel_all()
    db.reset()
    return {"ok": True}


# ==========================================================================
# Twilio webhooks (the live call)
# ==========================================================================
def _url(path: str, **params) -> str:
    # Relative URLs work: Twilio resolves them against the current webhook address.
    return f"{path}?{urlencode(params)}"


def _listen(call: dict, lang: str, texts: list, attempt: int, q: int, u: int) -> Response:
    params = dict(call_id=call["id"], attempt=attempt, q=q, u=u)
    return xml(gather_twiml(lang, texts, _url("/twilio/gather", **params), _url("/twilio/noinput", **params)))


@app.post("/twilio/voice")
async def twilio_voice(call_id: str, attempt: int = 1):
    """Person answered: speak the reminder and ask 'Did you do it?'."""
    c = db.get_call(call_id)
    if not c:
        return xml(hangup_twiml("en", ["Sorry, this reminder no longer exists. Goodbye."]))
    lang = lang_of(c)
    sender = c.get("sender_name") or p(lang, "family")
    intro = p(lang, "intro", sender=sender)
    db.add_event(call_id, "TWILIO", "Call answered.")
    db.add_event(call_id, "CARECALL", f"{intro} {c['spoken_message']}")
    return _listen(c, lang, [intro, c["spoken_message"], p(lang, "ask")], attempt, 0, 0)


@app.post("/twilio/gather")
async def twilio_gather(request: Request, call_id: str, attempt: int = 1, q: int = 0, u: int = 0):
    """Person replied (speech or keypad): Listener decides, system acts."""
    form = await request.form()
    speech = str(form.get("SpeechResult") or "")
    digits = str(form.get("Digits") or "")
    c = db.get_call(call_id)
    if not c:
        return xml(hangup_twiml("en", ["Goodbye."]))
    lang = lang_of(c)

    db.add_event(call_id, "PERSON", f"pressed {digits}" if digits else f"\"{speech}\"")
    res = listener_agent(c, lang, speech, digits)
    detail = f" {clamp_minutes(res['minutes'])} min" if res["outcome"] == "LATER" else ""
    db.add_event(call_id, "LISTENER", f"{res['outcome']}{detail} [{res['source']}]")
    outcome = res["outcome"]

    if outcome == "DONE":
        db.update_call(call_id, status="confirmed", next_attempt_at=None)
        db.add_event(call_id, "CARECALL", p(lang, "done"))
        return xml(hangup_twiml(lang, [p(lang, "done")]))

    if outcome == "LATER":
        minutes = clamp_minutes(res["minutes"])
        scheduler.reschedule(call_id, minutes)
        text = p(lang, "later", m=minutes)
        db.add_event(call_id, "CARECALL", text)
        return xml(hangup_twiml(lang, [text]))

    if outcome == "WORRYING":
        db.update_call(call_id, status="needs_attention", next_attempt_at=None)
        db.add_event(call_id, "SYSTEM", "Marked NEEDS ATTENTION. No escalation; the sender will see this.")
        db.add_event(call_id, "CARECALL", p(lang, "worrying"))
        return xml(hangup_twiml(lang, [p(lang, "worrying")]))

    if outcome == "QUESTION" and q < MAX_QUESTIONS:
        verdict = guardian_agent(c, lang, speech, res["reply"])
        if verdict["ok"]:
            db.add_event(call_id, "GUARDIAN", f"PASSED: {verdict['reason']} [{verdict['source']}]")
        else:
            db.add_event(call_id, "GUARDIAN",
                         f"BLOCKED \"{res['reply']}\": {verdict['reason']} [{verdict['source']}]")
        answer = verdict["final_answer"]
        db.add_event(call_id, "CARECALL", answer)
        return _listen(c, lang, [answer, p(lang, "reask")], attempt, q + 1, u)

    # UNCLEAR, or too many questions
    return _unclear(c, lang, attempt, q, u, "Unclear reply" if outcome == "UNCLEAR" else "Too many questions")


@app.post("/twilio/noinput")
async def twilio_noinput(call_id: str, attempt: int = 1, q: int = 0, u: int = 0):
    """Person said nothing."""
    c = db.get_call(call_id)
    if not c:
        return xml(hangup_twiml("en", ["Goodbye."]))
    db.add_event(call_id, "PERSON", "(silence)")
    return _unclear(c, lang_of(c), attempt, q, u, "No reply")


def _unclear(c: dict, lang: str, attempt: int, q: int, u: int, reason: str) -> Response:
    if u < MAX_UNCLEAR:
        db.add_event(c["id"], "CARECALL", p(lang, "unclear"))
        return _listen(c, lang, [p(lang, "unclear")], attempt, q, u + 1)
    db.add_event(c["id"], "CARECALL", p(lang, "bye_unclear"))
    scheduler.handle_failure(c["id"], reason)
    return xml(hangup_twiml(lang, [p(lang, "bye_unclear")]))


@app.post("/twilio/status")
async def twilio_status(request: Request, background: BackgroundTasks, call_id: str, attempt: int = 1):
    """Twilio says the call ended (completed, no-answer, busy, failed, canceled)."""
    form = await request.form()
    status = str(form.get("CallStatus") or "completed")
    duration = str(form.get("CallDuration") or "0")
    c = db.get_call(call_id)
    if not c:
        return {"ok": True}
    if int(c.get("attempts") or 0) != attempt:
        return {"ok": True, "ignored": "old attempt"}
    db.add_event(call_id, "TWILIO", f"Call ended: {status} ({duration} s).")
    if c["status"] == "calling":
        # nobody gave a final answer on this attempt
        scheduler.handle_failure(call_id, {"no-answer": "No answer", "busy": "Busy / declined",
                                           "failed": "Call failed", "canceled": "Call canceled"}
                                 .get(status, "Hung up without answering"))
    background.add_task(run_reporter, call_id)
    return {"ok": True}


def run_reporter(call_id: str):
    c = db.get_call(call_id)
    if not c:
        return
    summary = reporter_agent(c, db.list_events([call_id]))
    db.update_call(call_id, summary=summary)
    db.add_event(call_id, "REPORTER", summary)
