"""CareCall backend: FastAPI app.

Endpoints:
  /api/...           Used by the dashboard (contacts, calls, alerts, call_results)
  /twilio/voice      Twilio Voice webhook: Initial contact check ("May I speak with <name>?")
  /twilio/respond    Twilio Gather webhook: Identity state machine & check-in conversation
  /twilio/status     Twilio Status callback: Attempt tracking, 10m/30m retries, unreachable alerts
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
from twilio.twiml.voice_response import VoiceResponse, Gather

import config
import scheduler
from agents import clamp_minutes, guardian_agent, listener_agent, planner_agent, reporter_agent
from conversation import (
    is_emergency,
    classify_identity_reply,
    verify_secret_in_code,
    generate_checkin_turn,
    evaluate_call_results,
)
from db import BACKEND_NAME, db, now_iso
from phrases import LANGS, lang_of, p
from telephony import gather_twiml, hangup_twiml


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


def xml(twiml_obj) -> Response:
    return Response(content=str(twiml_obj), media_type="text/xml; charset=utf-8")


# ==========================================================================
# Dashboard & Management APIs
# ==========================================================================
class CallIn(BaseModel):
    phone: str
    message: str
    language: str = "en"
    scheduled_at: str
    note: str = ""
    sender_name: str = ""


class ContactIn(BaseModel):
    name: str
    phone: str
    secret_check: str = "1985"
    language: str = "en-IN"
    reminders: str = ""
    notes: str = ""
    caregiver_contact: str = ""


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "dry_run": config.DRY_RUN,
        "demo_mode": config.DEMO_MODE,
        "db": BACKEND_NAME,
        "llm": config.LLM_PROVIDER,
        "public_url": config.get_public_url(),
        "languages": config.ENABLED_LANGUAGES,
        "jobs": len(scheduler.sched.get_jobs()),
        "alerts_count": len(db.list_alerts()),
    }


# Contacts API
@app.post("/api/contacts")
def create_contact(body: ContactIn):
    cid = str(uuid.uuid4())
    row = {
        "id": cid,
        "name": body.name.strip(),
        "phone": body.phone.strip(),
        "secret_check": body.secret_check.strip(),
        "language": body.language,
        "reminders": body.reminders.strip(),
        "notes": body.notes.strip(),
        "caregiver_contact": body.caregiver_contact.strip(),
    }
    return db.create_contact(row)


@app.get("/api/contacts")
def list_contacts():
    return db.list_contacts()


# Alerts API (For caregiver dashboard)
@app.get("/api/alerts")
def list_alerts():
    return db.list_alerts()


# Call Results API (Structured call extraction history)
@app.get("/api/call-results")
def list_call_results(contact_id: str | None = None):
    if contact_id:
        return db.get_recent_results(contact_id, limit=10)
    return []


# Calls Scheduling API (existing)
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
        when = when.astimezone()
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


class ImmediateCallIn(BaseModel):
    phone: str
    message: str = "Daily check-in call"
    name: str = ""
    note: str = ""


@app.post("/api/calls/immediate")
def immediate_call(body: ImmediateCallIn):
    """Place an outbound check-in call immediately without waiting for scheduler."""
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise HTTPException(400, "Phone must be in international format, e.g. +918147581686")
    call_id = str(uuid.uuid4())
    row = {
        "id": call_id,
        "phone": phone,
        "message": body.message.strip() or "Daily check-in call",
        "note": body.note.strip(),
        "sender_name": body.name.strip() or "Contact",
        "language": "en",
        "spoken_message": body.message.strip() or "Daily check-in call",
        "priority": "normal",
        "priority_reason": "Immediate check-in",
        "max_attempts": 2,
        "retry_gap_seconds": 20,
        "attempts": 1,
        "failed_attempts": 0,
        "status": "calling",
        "scheduled_at": now_iso(),
        "next_attempt_at": now_iso(),
        "summary": "",
        "twilio_sid": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    db.insert_call(row)
    db.add_event(call_id, "SCHEDULER", f"Immediate call placed to {phone}.")
    try:
        import telephony
        sid = telephony.place_call(row, 1)
        db.update_call(call_id, twilio_sid=sid)
        db.add_event(call_id, "TWILIO", f"Phone ringing (Call SID: {sid}).")
        return {"ok": True, "call_id": call_id, "twilio_sid": sid, "status": "ringing"}
    except Exception as e:
        db.add_event(call_id, "SYSTEM", f"Failed to place call: {str(e)[:150]}")
        raise HTTPException(500, f"Twilio error: {str(e)}")


@app.delete("/api/calls")
def reset_all():
    scheduler.cancel_all()
    db.reset()
    return {"ok": True}


# ==========================================================================
# Twilio Conversational Webhooks (The 3 Hurdles Implementation)
# ==========================================================================

@app.api_route("/twilio/voice", methods=["GET", "POST"])
async def twilio_voice(request: Request, contact_id: str | None = None, call_id: str | None = None):
    """Start of Call: Asks who is speaking before disclosing any private reminder."""
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass
    call_sid = str(form.get("CallSid") or str(request.query_params.get("CallSid", "test-sid")))

    contact = None
    c = db.get_call(call_id) if call_id else None
    if contact_id:
        contact = db.get_contact(contact_id)
    if not contact and c:
        contact = db.get_contact_by_phone(c["phone"])
        if not contact:
            contact = {
                "id": str(uuid.uuid4()),
                "name": c.get("sender_name") or "sritej",
                "phone": c["phone"],
                "secret_check": "2007",
                "language": "en-IN",
                "reminders": c.get("message", ""),
                "notes": c.get("note", ""),
                "caregiver_contact": c.get("sender_name", ""),
            }
            db.create_contact(contact)
    if not contact:
        contacts = db.list_contacts()
        if contacts:
            contact = contacts[0]
        else:
            contact = {
                "id": str(uuid.uuid4()),
                "name": "sritej",
                "phone": "+918147581686",
                "secret_check": "2007",
                "language": "en-IN",
                "reminders": "Take your BP tablets after breakfast and drink some water",
                "notes": "Enjoys watching sunrise",
                "caregiver_contact": "+918147581686",
            }
            try:
                db.create_contact(contact)
            except Exception:
                pass

    name = (c.get("sender_name") if c and c.get("sender_name") else contact.get("name")) or "sritej"
    lang = contact.get("language", "en-IN")

    # STRICT INITIAL PROMPT: First say hello, then wait for keyword
    ask_identity = "Hello! May I know who is speaking? Please state your name."
    db.upsert_session(call_sid, contact["id"], state="verify", history=[{"role": "assistant", "text": ask_identity}])
    if call_id:
        db.add_event(call_id, "TWILIO", "Call answered. Asking for identity keyword.")

    base = config.get_public_url()
    action_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={call_sid}&stage=verify" + (f"&call_id={call_id}" if call_id else "")
    noinput_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={call_sid}&stage=verify&noinput=1" + (f"&call_id={call_id}" if call_id else "")

    vr = VoiceResponse()
    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language=lang,
        speech_timeout="auto",
        timeout=6,
    )
    g.say(ask_identity, voice="alice", language=lang)
    vr.append(g)
    # If silence / no input, redirect to noinput handler
    vr.redirect(noinput_url, method="POST")
    return xml(vr)


@app.api_route("/twilio/respond", methods=["GET", "POST"])
async def twilio_respond(request: Request, background: BackgroundTasks, contact_id: str | None = None, call_sid: str | None = None, call_id: str | None = None, stage: str | None = None, noinput: str | None = None):
    """Conversational turn handler: Verifies keyword, reads SpeechResult, updates SQLite transcript, calls Groq."""
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass
    speech = str(form.get("SpeechResult") or request.query_params.get("SpeechResult") or form.get("Digits") or "").strip()
    curr_stage = stage or request.query_params.get("stage") or "chat"
    is_noinput = bool(noinput or request.query_params.get("noinput") or form.get("noinput"))
    
    c = db.get_call(call_id) if call_id else None
    if not contact_id and c:
        ct = db.get_contact_by_phone(c["phone"])
        if ct: contact_id = ct["id"]
    
    contact = db.get_contact(contact_id) if contact_id else None
    if not contact:
        contacts = db.list_contacts()
        contact = contacts[0] if contacts else {"id": "default", "name": "sritej", "language": "en-IN"}

    sid = call_sid or str(form.get("CallSid") or request.query_params.get("CallSid") or "test-sid")
    session = db.get_session(sid)
    if not session:
        session = {"state": curr_stage, "history": []}

    history = session.get("history", [])
    vr = VoiceResponse()
    lang = contact.get("language", "en-IN")
    name = contact.get("name", "sritej")
    base = config.get_public_url()

    # -------------------------------------------------------------------------
    # STAGE 1: IDENTITY KEYWORD GATE
    # -------------------------------------------------------------------------
    if curr_stage == "verify":
        from conversation import is_contact_verified_by_keyword

        # Case A: Silence / Nothing spoken -> Mark as Alert & Cut Call
        if is_noinput or not speech:
            alert_reason = f"No response received during identity verification for {name}. Nobody answered or recipient remained silent."
            db.create_alert(contact["id"], "high", [alert_reason])
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"ALERT: Silent/No response received. Alerted caregiver.")
            silence_reply = "No response received. For your security, ending call. Goodbye."
            vr.say(silence_reply, voice="alice", language=lang)
            vr.hangup()
            return xml(vr)

        # Log spoken text
        history.append({"role": "user", "text": speech})
        if call_id:
            db.add_event(call_id, "CONTACT", f'"{speech}"')

        # Check if keyword ('sritej') is present
        verified = is_contact_verified_by_keyword(speech, name, contact.get("secret_check"))

        # Case B: Correct keyword ('sritej') spoken -> Proceed
        if verified:
            reminder = (c.get("spoken_message") if c else "") or (c.get("message") if c else "") or contact.get("reminders") or "Daily check-in call"
            verified_reply = f"Identity verified! Hello {name}. I am calling with your reminder: {reminder}. How are you feeling today?"
            history.append({"role": "assistant", "text": verified_reply})
            db.upsert_session(sid, contact["id"], state="chat", history=history)
            
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"Identity verified via keyword '{name}' (spoken: '{speech}').")
                db.add_event(call_id, "CARECALL", f"Delivered reminder: '{reminder}'.")

            chat_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={sid}&stage=chat" + (f"&call_id={call_id}" if call_id else "")
            g = Gather(
                input="speech",
                action=chat_url,
                method="POST",
                language=lang,
                speech_timeout="auto",
                timeout=6,
            )
            g.say(verified_reply, voice="alice", language=lang)
            vr.append(g)
            vr.redirect(chat_url, method="POST")
            return xml(vr)

        # Case C: Said something other than 'sritej' -> Mark as Alert & Cut Call
        else:
            alert_reason = f"Unverified recipient answered call for {name}: '{speech}'. Keyword '{name}' missing. Reminders kept private."
            db.create_alert(contact["id"], "high", [alert_reason])
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"ALERT: Recipient said '{speech}' (keyword '{name}' missing). Cut call & alerted caregiver.")
            
            reject_msg = "Sorry, identity keyword was not recognized. For privacy, reminders cannot be disclosed. Goodbye."
            vr.say(reject_msg, voice="alice", language=lang)
            vr.hangup()
            return xml(vr)

    # -------------------------------------------------------------------------
    # STAGE 2: ONGOING CONVERSATION & SAFETY CHECKS
    # -------------------------------------------------------------------------
    if speech:
        history.append({"role": "user", "text": speech})
        if call_id:
            db.add_event(call_id, "CONTACT", f'"{speech}"')

    # Emergency check
    if is_emergency(speech):
        emergency_reply = "Please call emergency services at 112 right now. I am alerting your caregiver immediately."
        vr.say(emergency_reply, voice="alice", language=lang)
        vr.hangup()
        db.create_alert(contact["id"], "high", [f"EMERGENCY keywords detected during check-in call with {name}: '{speech}'"])
        if call_id:
            db.add_event(call_id, "SYSTEM", f"EMERGENCY detected: '{speech}'. Alerted caregiver.")
        background.add_task(evaluate_call_results, contact["id"], history)
        return xml(vr)

    # Wrap up check
    if any(w in speech.lower() for w in ["goodbye", "bye", "that's all", "nothing else", "talk later", "all done", "all set"]):
        goodbye_reply = f"Thank you for checking in with me today, {name}. Please take care and have a wonderful day! Goodbye."
        vr.say(goodbye_reply, voice="alice", language=lang)
        vr.hangup()
        background.add_task(evaluate_call_results, contact["id"], history)
        if call_id:
            db.update_call(call_id, status="confirmed")
            db.add_event(call_id, "TWILIO", "Check-in call completed successfully.")
        return xml(vr)

    action_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={sid}&stage=chat" + (f"&call_id={call_id}" if call_id else "")

    if not speech:
        reprompt = f"Hello {name}, I am here for your check-in. Did you take your reminders today, or how are you feeling?"
        history.append({"role": "assistant", "text": reprompt})
        db.upsert_session(sid, contact["id"], state="chat", history=history)
        g = Gather(
            input="speech",
            action=action_url,
            method="POST",
            language=lang,
            speech_timeout="auto",
            timeout=7
        )
        g.say(reprompt, voice="alice", language=lang)
        vr.append(g)
        return xml(vr)

    # Generate warm conversational response using LLM (Groq / Claude)
    reply = generate_checkin_turn(contact, history, speech)
    history.append({"role": "assistant", "text": reply})
    db.upsert_session(sid, contact["id"], state="chat", history=history)
    if call_id:
        db.add_event(call_id, "CARECALL", reply)

    # Conclude if enough turns
    if len(history) >= 6:
        conclude = f"{reply} Thank you for checking in with me today, {name}. Have a wonderful day. Goodbye!"
        vr.say(conclude, voice="alice", language=lang)
        vr.hangup()
        background.add_task(evaluate_call_results, contact["id"], history)
        if call_id:
            db.update_call(call_id, status="confirmed")
            db.add_event(call_id, "TWILIO", "Check-in completed.")
        return xml(vr)

    g = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language=lang,
        speech_timeout="auto",
        timeout=7
    )
    g.say(reply, voice="alice", language=lang)
    vr.append(g)
    return xml(vr)


# ----------------------------------------------------------------------
# HURDLE 3: Twilio Status Callback (Retry, Escalation, Failure Handling)
# ----------------------------------------------------------------------
@app.api_route("/twilio/status", methods=["GET", "POST"])
async def twilio_status(request: Request, background: BackgroundTasks, contact_id: str | None = None, call_id: str | None = None, attempt: int = 1):
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass
    status = str(form.get("CallStatus") or request.query_params.get("CallStatus") or "completed")
    duration = str(form.get("CallDuration") or request.query_params.get("CallDuration") or "0")
    call_sid = str(form.get("CallSid") or request.query_params.get("CallSid") or "")

    if contact_id:
        db.add_attempt(contact_id, attempt, call_sid, status)

    c = db.get_call(call_id) if call_id else None
    contact_name = (c.get("sender_name") if c else None) or "contact"
    cid = contact_id or (c.get("id") if c else "default")

    # If call failed to connect or was not answered
    if status in ("no-answer", "busy", "failed", "canceled"):
        db.create_alert(cid, "high", [f"Nobody answered call to {contact_name} ({status}). Please check on them."])
        if call_id:
            db.update_call(call_id, status="needs_attention")
            db.add_event(call_id, "SYSTEM", f"Nobody answered ({status}). Caregiver alert created.")

    # If completed, trigger post-call evaluation & comparison with earlier calls
    elif status == "completed":
        session = db.get_session(call_sid)
        history = session.get("history", []) if session else []
        if call_id and not history:
            events = db.list_events([call_id])
            history = [{"role": "user" if e["agent"] == "CONTACT" else "assistant", "text": e["text"]} for e in events]
        if cid:
            background.add_task(evaluate_call_results, cid, history)
        if call_id:
            db.update_call(call_id, status="confirmed")
            db.add_event(call_id, "TWILIO", "Call completed.")

    return {"ok": True}
