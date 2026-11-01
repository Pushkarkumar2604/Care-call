"""CareCall FastAPI backend.

Dashboard APIs:
  /api/health
  /api/contacts
  /api/calls
  /api/calls/immediate
  /api/alerts
  /api/call-results

Twilio Webhooks (The 3 Competition Hurdles):
  /twilio/voice      - Hurdle 1: Asks for identity verification keyword (Birth Year 2007)
  /twilio/respond    - Hurdle 1 & 2: Validates 2007, Groq dialogue turn, emergency detection
  /twilio/status     - Hurdle 3: Unreachable call retry tracking & caregiver escalation
"""
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse, Gather

import config
import scheduler
from agents import planner_agent
from conversation import (
    is_emergency,
    is_contact_verified_by_keyword,
    generate_checkin_turn,
    evaluate_call_results,
)
from db import BACKEND_NAME, db, now_iso
from phrases import LANGS


# ============================================================
# APPLICATION LIFECYCLE
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    print(
        f"[carecall] DRY_RUN={config.DRY_RUN} "
        f"DEMO_MODE={config.DEMO_MODE} "
        f"DB={BACKEND_NAME} "
        f"LLM={config.LLM_PROVIDER} "
        f"PUBLIC_URL={config.get_public_url() or '(not set)'}"
    )
    yield
    scheduler.shutdown()


app = FastAPI(title="CareCall", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def xml(twiml_obj) -> Response:
    return Response(
        content=str(twiml_obj),
        media_type="text/xml; charset=utf-8",
    )


def normalize_phone(phone: str) -> str:
    phone = re.sub(r"[\s\-()]", "", phone)
    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise HTTPException(
            status_code=400,
            detail="Phone must be in international format, for example +918147581686",
        )
    return phone


# ============================================================
# CAREGIVER RECEIVERS (Team Carpe Diem Escalation Network)
# ============================================================

CAREGIVER_RECEIVERS = [
    {"name": "Sritej Deverkonda", "phone": "+918147581686", "role": "Lead Coordinator"},
    {"name": "Satyam Tiwari", "phone": "+919532981656", "role": "Caregiver Receiver"},
    {"name": "Pushkar Singh", "phone": "+917425849166", "role": "Caregiver Receiver"},
]


# ============================================================
# REQUEST MODELS
# ============================================================

class CallIn(BaseModel):
    phone: str
    message: str
    scheduled_at: str
    language: str = "en"
    note: str = ""
    sender_name: str = ""
    voice: str = "Polly.Aditi"


class ContactIn(BaseModel):
    name: str
    phone: str
    secret_check: str = "2007"
    language: str = "en-IN"
    reminders: str = ""
    notes: str = ""
    caregiver_contact: str = "+919532981656, +917425849166"
    voice: str = "Polly.Aditi"


class ImmediateCallIn(BaseModel):
    phone: str
    message: str = "Daily check-in call"
    name: str = ""
    note: str = ""
    voice: str = "Polly.Aditi"


# ============================================================
# DASHBOARD SERVING
# ============================================================

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
STATIC_DIR = Path(__file__).resolve().parent / "static"

@app.get("/")
def index():
    # Prefer frontend/index.html, fallback to static/index.html
    f_path = FRONTEND_DIR / "index.html"
    if f_path.is_file():
        return FileResponse(f_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    s_path = STATIC_DIR / "index.html"
    if s_path.is_file():
        return FileResponse(s_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="Dashboard index.html not found.")


@app.get("/style.css")
def get_style():
    f_path = FRONTEND_DIR / "style.css"
    if f_path.is_file():
        return FileResponse(f_path, media_type="text/css", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    s_path = STATIC_DIR / "style.css"
    if s_path.is_file():
        return FileResponse(s_path, media_type="text/css", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="style.css not found.")


@app.get("/app.js")
def get_app():
    f_path = FRONTEND_DIR / "app.js"
    if f_path.is_file():
        return FileResponse(f_path, media_type="application/javascript", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    s_path = STATIC_DIR / "app.js"
    if s_path.is_file():
        return FileResponse(s_path, media_type="application/javascript", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="app.js not found.")


@app.get("/api/voices")
def list_voices():
    return config.AVAILABLE_VOICES


@app.get("/api/receivers")
def list_receivers():
    return CAREGIVER_RECEIVERS


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "dry_run": config.DRY_RUN,
        "demo_mode": config.DEMO_MODE,
        "db": BACKEND_NAME,
        "llm": config.LLM_PROVIDER,
        "public_url": config.get_public_url(),
        "default_voice": config.DEFAULT_VOICE,
        "voices": config.AVAILABLE_VOICES,
        "receivers": CAREGIVER_RECEIVERS,
        "languages": config.ENABLED_LANGUAGES,
        "jobs": len(scheduler.sched.get_jobs()),
        "alerts_count": len(db.list_alerts()),
    }


# ============================================================
# CONTACTS API
# ============================================================

@app.post("/api/contacts")
def create_contact(body: ContactIn):
    phone = normalize_phone(body.phone)
    cid = str(uuid.uuid4())
    row = {
        "id": cid,
        "name": body.name.strip(),
        "phone": phone,
        "secret_check": body.secret_check.strip() or "2007",
        "language": body.language,
        "reminders": body.reminders.strip(),
        "notes": body.notes.strip(),
        "caregiver_contact": body.caregiver_contact.strip() or phone,
        "created_at": now_iso(),
    }
    return db.create_contact(row)


@app.get("/api/contacts")
def list_contacts():
    return db.list_contacts()


# ============================================================
# ALERTS API (Caregiver Dashboard)
# ============================================================

@app.get("/api/alerts")
def list_alerts():
    return db.list_alerts()


@app.post("/api/alerts/{alert_id}/ack")
def ack_alert(alert_id: int):
    try:
        if hasattr(db, "sb") and db.sb:
            db.sb.table("alerts").update({"resolved": True}).eq("id", alert_id).execute()
        return {"ok": True, "alert_id": alert_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============================================================
# CALL RESULTS API (Longitudinal Analysis History)
# ============================================================

@app.get("/api/call-results")
def list_call_results(contact_id: str | None = None):
    if contact_id:
        return db.get_recent_results(contact_id, limit=10)
    return []


# ============================================================
# SCHEDULED CALLS API
# ============================================================

@app.post("/api/calls")
def create_call(body: CallIn):
    phone = normalize_phone(body.phone)
    try:
        when = datetime.fromisoformat(body.scheduled_at)
    except ValueError:
        raise HTTPException(400, "scheduled_at must be an ISO datetime string")

    lang = body.language.lower()
    if lang not in config.ENABLED_LANGUAGES:
        raise HTTPException(400, f"language must be one of {config.ENABLED_LANGUAGES}")

    call_id = str(uuid.uuid4())
    try:
        plan = planner_agent({"message": body.message, "note": body.note})
    except TypeError:
        plan = planner_agent(body.message, body.note)

    priority = plan.get("priority", "normal")
    priority_reason = plan.get("priority_reason") or plan.get("reason", "Standard check-in")
    spoken_message = plan.get("spoken_message") or body.message.strip()
    max_attempts = plan.get("max_attempts") or (3 if priority == "critical" else 2)
    retry_gap_seconds = plan.get("retry_gap_seconds") or (15 if priority == "critical" else 30)

    row = {
        "id": call_id,
        "phone": phone,
        "message": body.message.strip(),
        "note": body.note.strip(),
        "sender_name": body.sender_name.strip(),
        "language": lang,
        "spoken_message": spoken_message,
        "priority": priority,
        "priority_reason": priority_reason,
        "max_attempts": max_attempts,
        "retry_gap_seconds": retry_gap_seconds,
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
    db.add_event(call_id, "PLANNER", f"Priority {priority}: {priority_reason}")
    scheduler.schedule_attempt(call_id, when)
    return row


@app.get("/api/calls")
def list_calls():
    calls = db.list_calls()
    if not calls:
        return []
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
        raise HTTPException(404, "Call not found")
    scheduler.schedule_attempt(call_id, scheduler.utcnow())
    db.add_event(call_id, "SCHEDULER", "Manual 'call now' triggered.")
    return {"ok": True}


# ============================================================
# IMMEDIATE CALL API (Instant Dialing via Twilio)
# ============================================================

@app.post("/api/calls/immediate")
def immediate_call(body: ImmediateCallIn):
    phone = normalize_phone(body.phone)
    call_id = str(uuid.uuid4())
    row = {
        "id": call_id,
        "phone": phone,
        "message": body.message.strip() or "Daily check-in call",
        "note": body.note.strip(),
        "sender_name": body.name.strip() or "sritej",
        "language": "en",
        "spoken_message": body.message.strip() or "Daily check-in call",
        "voice": body.voice or config.DEFAULT_VOICE,
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


@app.post("/api/calls/immediate-all")
def immediate_call_all(body: ImmediateCallIn):
    """Dial all 3 receivers simultaneously (Broadcast check-in)."""
    import telephony
    results = []
    default_rec_msgs = {
        "+918147581686": "Take your blood pressure tablets and drink warm water",
        "+919532981656": "Take your multivitamin tablets and drink water",
        "+917425849166": "Post-lunch walking check-in and hydration",
    }
    for r in CAREGIVER_RECEIVERS:
        phone = normalize_phone(r["phone"])
        call_id = str(uuid.uuid4())
        msg = body.message.strip() if body.message.strip() and body.message.strip() != "Daily check-in call" else default_rec_msgs.get(phone, "Daily check-in call")
        row = {
            "id": call_id,
            "phone": phone,
            "message": msg,
            "note": body.note.strip(),
            "sender_name": r["name"],
            "language": "en",
            "spoken_message": msg,
            "voice": body.voice or config.DEFAULT_VOICE,
            "priority": "normal",
            "priority_reason": "Immediate broadcast check-in",
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
        db.add_event(call_id, "SCHEDULER", f"Broadcast call placed to {r['name']} ({phone}).")
        try:
            sid = telephony.place_call(row, 1)
            db.update_call(call_id, twilio_sid=sid)
            db.add_event(call_id, "TWILIO", f"Phone ringing (Call SID: {sid}).")
            results.append({"name": r["name"], "phone": phone, "call_id": call_id, "twilio_sid": sid, "status": "ringing"})
        except Exception as e:
            db.add_event(call_id, "SYSTEM", f"Broadcast failed for {phone}: {str(e)[:150]}")
            results.append({"name": r["name"], "phone": phone, "error": str(e), "status": "failed"})
    return {"ok": True, "results": results}


@app.delete("/api/calls")
def reset_all():
    scheduler.cancel_all()
    db.reset()
    return {"ok": True}


# ============================================================
# HURDLE 1: TWILIO VOICE WEBHOOK (Birth Year 2007 Gate)
# ============================================================

@app.api_route("/twilio/voice", methods=["GET", "POST"])
async def twilio_voice(request: Request, contact_id: str | None = None, call_id: str | None = None):
    """Start of Call: Asks for identity verification (Birth Year 2007) before revealing private reminder."""
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
            try:
                db.create_contact(contact)
            except Exception:
                pass

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
    tts_voice = request.query_params.get("voice") or (c.get("voice") if c else None) or (contact.get("voice") if contact else None) or config.DEFAULT_VOICE

    # STRICT INITIAL PROMPT: First ask for birth year, then wait and listen
    ask_identity = "Hello! This is CareCall. May I verify your identity? Please state your year of birth."
    db.upsert_session(call_sid, contact["id"], state="verify", history=[{"role": "assistant", "text": ask_identity}])
    if call_id:
        db.add_event(call_id, "TWILIO", f"Call answered. Asking for birth year keyword (2007) with voice {tts_voice}.")

    base = config.get_public_url()
    action_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={call_sid}&stage=verify&voice={tts_voice}" + (f"&call_id={call_id}" if call_id else "")
    noinput_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={call_sid}&stage=verify&noinput=1&voice={tts_voice}" + (f"&call_id={call_id}" if call_id else "")

    vr = VoiceResponse()
    g = Gather(
        input="speech dtmf",
        action=action_url,
        method="POST",
        language=lang,
        speech_timeout="auto",
        timeout=6,
    )
    g.say(ask_identity, voice=tts_voice, language=lang)
    vr.append(g)
    vr.redirect(noinput_url, method="POST")
    return xml(vr)


# ============================================================
# HURDLES 1 & 2: TWILIO RESPOND WEBHOOK
# ============================================================

@app.api_route("/twilio/respond", methods=["GET", "POST"])
async def twilio_respond(
    request: Request,
    background: BackgroundTasks,
    contact_id: str | None = None,
    call_sid: str | None = None,
    call_id: str | None = None,
    stage: str | None = None,
    noinput: str | None = None,
):
    """Conversational turn handler: Verifies 2007, updates transcript, handles Groq LLM check-in turns & emergency."""
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
        if ct:
            contact_id = ct["id"]

    contact = db.get_contact(contact_id) if contact_id else None
    if not contact:
        contacts = db.list_contacts()
        contact = contacts[0] if contacts else {"id": "default", "name": "sritej", "language": "en-IN", "secret_check": "2007"}

    tts_voice = request.query_params.get("voice") or (c.get("voice") if c else None) or (contact.get("voice") if contact else None) or config.DEFAULT_VOICE

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
    # HURDLE 1: BIRTH YEAR KEYWORD GATE (2007)
    # -------------------------------------------------------------------------
    if curr_stage == "verify":
        # Case A: Silence / No response received -> Create Alert & Cut Call
        if is_noinput or not speech:
            alert_reason = f"No response received during birth year verification for {name}. Nobody answered or recipient remained silent. Caregivers notified: Satyam (+919532981656), Pushkar (+917425849166), Sritej."
            db.create_alert(contact["id"], "high", [alert_reason])
            if call_id:
                db.add_event(call_id, "GUARDIAN", "ALERT: Silent/No response for birth year check. Dispatched to caregivers Satyam, Pushkar, Sritej.")
            silence_reply = "No response received. For your security, ending call. Goodbye."
            vr.say(silence_reply, voice=tts_voice, language=lang)
            vr.hangup()
            return xml(vr)

        # Log spoken input
        history.append({"role": "user", "text": speech})
        if call_id:
            db.add_event(call_id, "CONTACT", f'"{speech}"')

        # Check birth year (2007) or name
        verified = is_contact_verified_by_keyword(speech, name, contact.get("secret_check", "2007"))

        # Case B: Correct keyword ('2007') spoken/dialed -> Proceed to Reminder & Chat
        if verified:
            reminder = (
                (c.get("spoken_message") if c else "")
                or (c.get("message") if c else "")
                or contact.get("reminders")
                or "Daily check-in call"
            )
            verified_reply = f"Identity verified! Hello {name}. I am calling with your reminder: {reminder}. How are you feeling today?"
            history.append({"role": "assistant", "text": verified_reply})
            db.upsert_session(sid, contact["id"], state="chat", history=history)

            if call_id:
                db.add_event(call_id, "GUARDIAN", f"Identity verified via birth year 2007 (spoken: '{speech}').")
                db.add_event(call_id, "CARECALL", f"Delivered reminder: '{reminder}'.")

            chat_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={sid}&stage=chat&voice={tts_voice}" + (f"&call_id={call_id}" if call_id else "")
            g = Gather(
                input="speech dtmf",
                action=chat_url,
                method="POST",
                language=lang,
                speech_timeout="auto",
                timeout=6,
            )
            g.say(verified_reply, voice=tts_voice, language=lang)
            vr.append(g)
            vr.redirect(chat_url, method="POST")
            return xml(vr)

        # Case C: Said something other than '2007' -> Create Alert & Cut Call
        else:
            alert_reason = f"Unverified recipient answered call for {name}: '{speech}'. Expected birth year 2007. Caregivers notified: Satyam (+919532981656), Pushkar (+917425849166), Sritej."
            db.create_alert(contact["id"], "high", [alert_reason])
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"ALERT: Recipient said '{speech}' (birth year 2007 missing). Cut call & dispatched to caregivers Satyam, Pushkar, Sritej.")

            reject_msg = "Sorry, birth year was not verified. For privacy, reminders cannot be disclosed. Goodbye."
            vr.say(reject_msg, voice=tts_voice, language=lang)
            vr.hangup()
            return xml(vr)

    # -------------------------------------------------------------------------
    # HURDLE 2: ONGOING CONVERSATION, EMERGENCY DETECTION & WRAP-UP
    # -------------------------------------------------------------------------
    if speech:
        history.append({"role": "user", "text": speech})
        if call_id:
            db.add_event(call_id, "CONTACT", f'"{speech}"')

    # Emergency keyword detection (Hurdle 2 safety gate)
    if is_emergency(speech):
        emergency_reply = "Please call emergency services at 112 right now. I am alerting your caregivers immediately."
        vr.say(emergency_reply, voice=tts_voice, language=lang)
        vr.hangup()
        db.create_alert(contact["id"], "high", [f"EMERGENCY keywords detected during check-in with {name}: '{speech}'. Alerted caregivers: Satyam (+919532981656), Pushkar (+917425849166), Sritej."])
        if call_id:
            db.add_event(call_id, "SYSTEM", f"EMERGENCY detected: '{speech}'. Dispatched to caregivers Satyam, Pushkar, Sritej.")
        background.add_task(evaluate_call_results, contact["id"], history)
        return xml(vr)

    # Wrap up check
    if any(w in speech.lower() for w in ["goodbye", "bye", "that's all", "nothing else", "talk later", "all done", "all set"]):
        goodbye_reply = f"Thank you for checking in with me today, {name}. Please take care and have a wonderful day! Goodbye."
        vr.say(goodbye_reply, voice=tts_voice, language=lang)
        vr.hangup()
        background.add_task(evaluate_call_results, contact["id"], history)
        if call_id:
            db.update_call(call_id, status="confirmed")
            db.add_event(call_id, "TWILIO", "Check-in call completed successfully.")
        return xml(vr)

    action_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={sid}&stage=chat&voice={tts_voice}" + (f"&call_id={call_id}" if call_id else "")

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
            timeout=7,
        )
        g.say(reprompt, voice=tts_voice, language=lang)
        vr.append(g)
        return xml(vr)

    # Generate warm conversational turn using LLM (Groq / Claude)
    reply = generate_checkin_turn(contact, history, speech)
    history.append({"role": "assistant", "text": reply})
    db.upsert_session(sid, contact["id"], state="chat", history=history)
    if call_id:
        db.add_event(call_id, "CARECALL", reply)

    # Conclude after enough conversational turns
    if len(history) >= 6:
        conclude = f"{reply} Thank you for checking in with me today, {name}. Have a wonderful day. Goodbye!"
        vr.say(conclude, voice=tts_voice, language=lang)
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
        timeout=7,
    )
    g.say(reply, voice=tts_voice, language=lang)
    vr.append(g)
    return xml(vr)


# ============================================================
# HURDLE 3: TWILIO STATUS CALLBACK (Retry & Escalation)
# ============================================================

@app.api_route("/twilio/status", methods=["GET", "POST"])
async def twilio_status(
    request: Request,
    background: BackgroundTasks,
    contact_id: str | None = None,
    call_id: str | None = None,
    attempt: int = 1,
):
    form = {}
    try:
        form = await request.form()
    except Exception:
        pass

    status = str(form.get("CallStatus") or request.query_params.get("CallStatus") or "completed")
    call_sid = str(form.get("CallSid") or request.query_params.get("CallSid") or "")

    if contact_id:
        db.add_attempt(contact_id, attempt, call_sid, status)

    call = db.get_call(call_id) if call_id else None
    contact_name = (call.get("sender_name") if call else None) or "contact"
    cid = contact_id or (call.get("id") if call else "default")

    # If call failed to connect or was not answered -> Create Caregiver Alert
    if status in ("no-answer", "busy", "failed", "canceled"):
        db.create_alert(cid, "high", [f"Nobody answered call to {contact_name} ({status}). Alerted caregivers: Satyam (+919532981656), Pushkar (+917425849166), Sritej (+918147581686)."])
        if call_id:
            db.update_call(call_id, status="needs_attention")
            db.add_event(call_id, "SYSTEM", f"Nobody answered ({status}). Caregiver alert created.")

    # If completed, trigger post-call longitudinal evaluation
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
            db.add_event(call_id, "TWILIO", "Check-in call completed successfully.")

    return {"ok": True, "status": status}
