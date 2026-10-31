"""CareCall FastAPI backend.

Dashboard APIs:
  /api/health
  /api/contacts
  /api/calls
  /api/alerts
  /api/call-results

Twilio webhooks:
  /twilio/voice
  /twilio/respond
  /twilio/status

IMPORTANT:
  Keep DRY_RUN=true and DEMO_MODE=true for safe testing.
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
from agents import (
    planner_agent,
)
from conversation import (
    is_emergency,
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
    allow_methods=["*"],
    allow_headers=["*"],
)


def xml(twiml_obj):
    return Response(
        content=str(twiml_obj),
        media_type="text/xml; charset=utf-8",
    )


def require_dry_run():
    """Prevent manual call actions from initiating real calls."""
    if not config.DRY_RUN or not config.DEMO_MODE:
        raise HTTPException(
            status_code=503,
            detail=(
                "This testing endpoint requires both "
                "DRY_RUN=true and DEMO_MODE=true."
            ),
        )


def normalize_phone(phone: str) -> str:
    phone = re.sub(r"[\s\-()]", "", phone)

    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise HTTPException(
            status_code=400,
            detail=(
                "Phone must be in international format, "
                "for example +919876543210"
            ),
        )

    return phone


# ============================================================
# REQUEST MODELS
# ============================================================

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


class ImmediateCallIn(BaseModel):
    phone: str
    message: str = "Daily check-in call"
    name: str = ""
    note: str = ""


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/")
def index():
    frontend = Path(__file__).parent / "static" / "index.html"

    if not frontend.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Dashboard file not found. "
                "Place index.html in backend/static/."
            ),
        )

    return FileResponse(frontend)


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


# ============================================================
# CONTACTS
# ============================================================

@app.post("/api/contacts")
def create_contact(body: ContactIn):
    contact_id = str(uuid.uuid4())

    row = {
        "id": contact_id,
        "name": body.name.strip(),
        "phone": normalize_phone(body.phone),
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


# ============================================================
# ALERTS
# ============================================================

@app.get("/api/alerts")
def list_alerts():
    return db.list_alerts()


# ============================================================
# CALL RESULTS
# ============================================================

@app.get("/api/call-results")
def list_call_results(contact_id: str | None = None):
    if contact_id:
        return db.get_recent_results(contact_id, limit=10)

    return []


# ============================================================
# SCHEDULED REMINDERS
# ============================================================

@app.post("/api/calls")
def create_call(body: CallIn):
    phone = normalize_phone(body.phone)

    message = body.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Reminder message cannot be empty.",
        )

    language = (
        body.language
        if body.language in LANGS
        else "en"
    )

    try:
        when = datetime.fromisoformat(
            body.scheduled_at.replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="scheduled_at must be an ISO date-time.",
        )

    if when.tzinfo is None:
        when = when.astimezone()

    minimum_time = scheduler.utcnow() + timedelta(seconds=5)

    if when < minimum_time:
        when = minimum_time

    # Generate the reminder plan.
    try:
        plan = planner_agent(message, language)
    except Exception as exc:
        print(f"[planner] Error: {exc}")

        raise HTTPException(
            status_code=502,
            detail="The reminder planner failed. Check backend logs.",
        )

    call_id = str(uuid.uuid4())

    row = {
        "id": call_id,
        "phone": phone,
        "message": message,
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

    db.add_event(
        call_id,
        "PLANNER",
        (
            f"Priority {plan['priority'].upper()} "
            f"({plan['reason']}). Plan: up to "
            f"{plan['max_attempts']} attempts, "
            f"{plan['retry_gap_seconds']} seconds apart. "
            f"[{plan.get('source', 'unknown')}]"
        ),
    )

    try:
        scheduler.schedule_attempt(call_id, when)

        db.add_event(
            call_id,
            "SCHEDULER",
            f"Queued for {when.astimezone():%d %b %H:%M:%S}.",
        )

    except Exception as exc:
        print(f"[scheduler] Could not schedule {call_id}: {exc}")

        db.add_event(
            call_id,
            "SYSTEM",
            f"Scheduling error: {str(exc)[:200]}",
        )

        raise HTTPException(
            status_code=500,
            detail="Reminder saved, but scheduling failed. Check backend logs.",
        )

    return db.get_call(call_id)


@app.get("/api/calls")
def list_calls():
    calls = db.list_calls()

    events = db.list_events([
        call["id"] for call in calls
    ])

    events_by_call = {}

    for event in events:
        events_by_call.setdefault(
            event["call_id"], []
        ).append(event)

    for call in calls:
        call["events"] = events_by_call.get(
            call["id"], []
        )

    return calls


@app.post("/api/calls/{call_id}/call-now")
def call_now(call_id: str):
    # Manual execution is disabled in dry-run mode.
    # Do not use this endpoint for testing.
    require_dry_run()

    call = db.get_call(call_id)

    if not call:
        raise HTTPException(
            status_code=404,
            detail="Reminder not found.",
        )

    if call["status"] not in (
        "scheduled",
        "rescheduled",
        "retrying",
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Reminder cannot be manually triggered "
                f"from status '{call['status']}'."
            ),
        )

    # Intentionally do not trigger the scheduler here.
    # Test reminders through their normal scheduled workflow.
    raise HTTPException(
        status_code=403,
        detail=(
            "Manual call triggering is disabled. "
            "Use the scheduled dry-run workflow instead."
        ),
    )


@app.post("/api/calls/immediate")
def immediate_call(body: ImmediateCallIn):
    """Create a simulated immediate-call record without dialing."""

    require_dry_run()

    phone = normalize_phone(body.phone)

    call_id = str(uuid.uuid4())
    message = body.message.strip() or "Daily check-in call"

    row = {
        "id": call_id,
        "phone": phone,
        "message": message,
        "note": body.note.strip(),
        "sender_name": body.name.strip() or "Contact",
        "language": "en",
        "spoken_message": message,
        "priority": "normal",
        "priority_reason": "Dry-run simulation",
        "max_attempts": 2,
        "retry_gap_seconds": 20,
        "attempts": 1,
        "failed_attempts": 0,
        "status": "confirmed",
        "scheduled_at": now_iso(),
        "next_attempt_at": now_iso(),
        "summary": "Simulated immediate call; no phone call was placed.",
        "twilio_sid": f"DRYRUN-{call_id[:8]}",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    db.insert_call(row)

    db.add_event(
        call_id,
        "SCHEDULER",
        "Dry-run immediate-call simulation. No phone call was placed.",
    )

    db.add_event(
        call_id,
        "DRY_RUN",
        "Simulated completion. Telephony was not invoked.",
    )

    return {
        "ok": True,
        "dry_run": True,
        "call_id": call_id,
        "twilio_sid": row["twilio_sid"],
        "status": "confirmed",
        "message": "Simulation completed. No real call was placed.",
    }


# ============================================================
# RESET
# ============================================================

@app.delete("/api/calls")
def reset_all():
    # Prevent accidental destruction of the reminder database.
    raise HTTPException(
        status_code=403,
        detail=(
            "Bulk reset is disabled in this API. "
            "Do not delete reminders through the dashboard."
        ),
    )


# ============================================================
# TWILIO VOICE WEBHOOK
# ============================================================

<<<<<<< HEAD
@app.api_route(
    "/twilio/voice",
    methods=["GET", "POST"],
)
async def twilio_voice(
    request: Request,
    contact_id: str | None = None,
    call_id: str | None = None,
):
=======
@app.api_route("/twilio/voice", methods=["GET", "POST"])
async def twilio_voice(request: Request, contact_id: str | None = None, call_id: str | None = None):
    """Start of Call: Asks who is speaking before disclosing any private reminder."""
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845
    form = {}

    try:
        form = await request.form()
    except Exception:
        pass

<<<<<<< HEAD
    call_sid = str(
        form.get("CallSid")
        or request.query_params.get("CallSid")
        or "test-sid"
    )

    call = db.get_call(call_id) if call_id else None

    contact = (
        db.get_contact(contact_id)
        if contact_id
        else None
    )

    if not contact and call:
        contact = db.get_contact_by_phone(call["phone"])

        if not contact:
            contact = {
                "id": str(uuid.uuid4()),
                "name": call.get("sender_name") or "there",
                "phone": call["phone"],
                "secret_check": "1985",
=======
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
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845
                "language": "en-IN",
                "reminders": call.get("message", ""),
                "notes": call.get("note", ""),
                "caregiver_contact": "",
            }

            db.create_contact(contact)

    if not contact:
        contacts = db.list_contacts()
<<<<<<< HEAD

        if contacts:
            contact = contacts[0]
        else:
            # Never create a hard-coded real contact or phone number.
            vr = VoiceResponse()
            vr.say(
                "CareCall contact not found. Goodbye.",
                voice="alice",
                language="en-IN",
            )
            vr.hangup()
            return xml(vr)

    name = (
        call.get("sender_name")
        if call and call.get("sender_name")
        else contact.get("name")
    ) or "there"

    reminder = ""

    if call:
        reminder = (
            call.get("spoken_message")
            or call.get("message")
            or ""
        )

    if not reminder:
        reminder = contact.get("reminders", "")

    if reminder:
        greeting = (
            f"Hello {name}, this is CareCall. "
            f"I am calling with your reminder: {reminder}. "
            "How are you feeling today?"
        )
    else:
        greeting = (
            f"Hello {name}, this is your CareCall check-in. "
            "How are you feeling today?"
        )

    db.upsert_session(
        call_sid,
        contact["id"],
        state="checkin",
        history=[
            {
                "role": "assistant",
                "text": greeting,
            }
        ],
    )

    if call_id:
        db.add_event(
            call_id,
            "TWILIO",
            "Voice webhook received.",
        )

    base = config.get_public_url()

    action_url = (
        f"{base}/twilio/respond?"
        f"contact_id={contact['id']}"
        f"&call_sid={call_sid}"
        + (f"&call_id={call_id}" if call_id else "")
    )

    response = VoiceResponse()

    gather = Gather(
        input="speech",
=======
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

    # STRICT INITIAL PROMPT: First say hello, then wait for birth year keyword
    ask_identity = "Hello! This is CareCall. May I verify your identity? Please state your year of birth."
    db.upsert_session(call_sid, contact["id"], state="verify", history=[{"role": "assistant", "text": ask_identity}])
    if call_id:
        db.add_event(call_id, "TWILIO", "Call answered. Asking for birth year verification (2007).")

    base = config.get_public_url()
    action_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={call_sid}&stage=verify" + (f"&call_id={call_id}" if call_id else "")
    noinput_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={call_sid}&stage=verify&noinput=1" + (f"&call_id={call_id}" if call_id else "")

    vr = VoiceResponse()
    g = Gather(
        input="speech dtmf",
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845
        action=action_url,
        method="POST",
        language=lang,
        speech_timeout="auto",
        timeout=6,
    )
<<<<<<< HEAD

    gather.say(
        greeting,
        voice="alice",
        language=contact.get("language", "en-IN"),
    )

    response.append(gather)
    response.redirect(action_url, method="POST")

    return xml(response)


# ============================================================
# TWILIO CONVERSATION WEBHOOK
# ============================================================

@app.api_route(
    "/twilio/respond",
    methods=["GET", "POST"],
)
async def twilio_respond(
    request: Request,
    background: BackgroundTasks,
    contact_id: str | None = None,
    call_sid: str | None = None,
    call_id: str | None = None,
):
=======
    g.say(ask_identity, voice="alice", language=lang)
    vr.append(g)
    # If silence / no input, redirect to noinput handler
    vr.redirect(noinput_url, method="POST")
    return xml(vr)


@app.api_route("/twilio/respond", methods=["GET", "POST"])
async def twilio_respond(request: Request, background: BackgroundTasks, contact_id: str | None = None, call_sid: str | None = None, call_id: str | None = None, stage: str | None = None, noinput: str | None = None):
    """Conversational turn handler: Verifies keyword, reads SpeechResult, updates SQLite transcript, calls Groq."""
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845
    form = {}

    try:
        form = await request.form()
    except Exception:
        pass
<<<<<<< HEAD

    speech = str(
        form.get("SpeechResult")
        or request.query_params.get("SpeechResult")
        or form.get("Digits")
        or ""
    ).strip()

    if not contact_id and call_id:
        call = db.get_call(call_id)

        if call:
            contact = db.get_contact_by_phone(
                call["phone"]
            )

            if contact:
                contact_id = contact["id"]

    contact = (
        db.get_contact(contact_id)
        if contact_id
        else None
    )

    if not contact:
        contacts = db.list_contacts()
=======
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
        contact = contacts[0] if contacts else {"id": "default", "name": "sritej", "language": "en-IN", "secret_check": "2007"}
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845

        if not contacts:
            response = VoiceResponse()
            response.say(
                "CareCall contact not found. Goodbye.",
                voice="alice",
                language="en-IN",
            )
            response.hangup()
            return xml(response)

        contact = contacts[0]

    sid = (
        call_sid
        or form.get("CallSid")
        or request.query_params.get("CallSid")
        or "test-sid"
    )

    session = db.get_session(sid)

    if not session:
<<<<<<< HEAD
        session = {
            "state": "checkin",
            "history": [],
        }

    history = session.get("history", [])

    response = VoiceResponse()

    language = contact.get("language", "en-IN")
    name = contact.get("name", "there")
=======
        session = {"state": curr_stage, "history": []}

    history = session.get("history", [])
    vr = VoiceResponse()
    lang = contact.get("language", "en-IN")
    name = contact.get("name", "sritej")
    base = config.get_public_url()
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845

    # -------------------------------------------------------------------------
    # STAGE 1: BIRTH YEAR KEYWORD GATE (2007)
    # -------------------------------------------------------------------------
    if curr_stage == "verify":
        from conversation import is_contact_verified_by_keyword

        # Case A: Silence / Nothing spoken -> Mark as Alert & Cut Call
        if is_noinput or not speech:
            alert_reason = f"No response received during birth year verification for {name}. Nobody answered or recipient remained silent."
            db.create_alert(contact["id"], "high", [alert_reason])
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"ALERT: Silent/No response for birth year check. Alerted caregiver.")
            silence_reply = "No response received. For your security, ending call. Goodbye."
            vr.say(silence_reply, voice="alice", language=lang)
            vr.hangup()
            return xml(vr)

        # Log spoken text
        history.append({"role": "user", "text": speech})
        if call_id:
            db.add_event(call_id, "CONTACT", f'"{speech}"')

        # Check if birth year keyword ('2007') is present
        verified = is_contact_verified_by_keyword(speech, name, contact.get("secret_check", "2007"))

        # Case B: Correct keyword ('2007') spoken -> Proceed
        if verified:
            reminder = (c.get("spoken_message") if c else "") or (c.get("message") if c else "") or contact.get("reminders") or "Daily check-in call"
            verified_reply = f"Identity verified! Hello {name}. I am calling with your reminder: {reminder}. How are you feeling today?"
            history.append({"role": "assistant", "text": verified_reply})
            db.upsert_session(sid, contact["id"], state="chat", history=history)
            
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"Identity verified via birth year 2007 (spoken: '{speech}').")
                db.add_event(call_id, "CARECALL", f"Delivered reminder: '{reminder}'.")

            chat_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={sid}&stage=chat" + (f"&call_id={call_id}" if call_id else "")
            g = Gather(
                input="speech dtmf",
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

        # Case C: Said something other than '2007' -> Mark as Alert & Cut Call
        else:
            alert_reason = f"Unverified recipient answered call for {name}: '{speech}'. Expected birth year 2007."
            db.create_alert(contact["id"], "high", [alert_reason])
            if call_id:
                db.add_event(call_id, "GUARDIAN", f"ALERT: Recipient said '{speech}' (birth year 2007 missing). Cut call & alerted caregiver.")
            
            reject_msg = "Sorry, birth year was not verified. For privacy, reminders cannot be disclosed. Goodbye."
            vr.say(reject_msg, voice="alice", language=lang)
            vr.hangup()
            return xml(vr)

    # -------------------------------------------------------------------------
    # STAGE 2: ONGOING CONVERSATION & SAFETY CHECKS
    # -------------------------------------------------------------------------
    if speech:
        history.append({
            "role": "user",
            "text": speech,
        })

        if call_id:
            db.add_event(
                call_id,
                "CONTACT",
                speech,
            )

    # Emergency handling.
    if is_emergency(speech):
        emergency_reply = (
            "Please contact emergency services at 112 "
            "if you are in immediate danger. "
            "I am alerting your caregiver."
        )

        response.say(
            emergency_reply,
            voice="alice",
            language=language,
        )

        response.hangup()

        db.create_alert(
            contact["id"],
            "high",
            [
                "Emergency keywords detected during "
                f"check-in with {name}."
            ],
        )

        if call_id:
            db.add_event(
                call_id,
                "SYSTEM",
                "Emergency keywords detected.",
            )

        background.add_task(
            evaluate_call_results,
            contact["id"],
            history,
        )

        return xml(response)

    # Conversation ending.
    goodbye_words = [
        "goodbye",
        "bye",
        "that's all",
        "nothing else",
        "talk later",
        "all done",
        "all set",
    ]

    if any(
        word in speech.lower()
        for word in goodbye_words
    ):
        reply = (
            f"Thank you for checking in today, {name}. "
            "Please take care. Goodbye."
        )

        response.say(
            reply,
            voice="alice",
            language=language,
        )

        response.hangup()

        background.add_task(
            evaluate_call_results,
            contact["id"],
            history,
        )

        if call_id:
            db.update_call(
                call_id,
                status="confirmed",
            )

            db.add_event(
                call_id,
                "TWILIO",
                "Check-in conversation completed.",
            )

        return xml(response)

<<<<<<< HEAD
    base = config.get_public_url()
=======
    action_url = f"{base}/twilio/respond?contact_id={contact['id']}&call_sid={sid}&stage=chat" + (f"&call_id={call_id}" if call_id else "")
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845

    action_url = (
        f"{base}/twilio/respond?"
        f"contact_id={contact['id']}"
        f"&call_sid={sid}"
        + (f"&call_id={call_id}" if call_id else "")
    )

    # No speech detected.
    if not speech:
<<<<<<< HEAD
        reprompt = (
            f"Hello {name}, I am here for your check-in. "
            "Did you take your medicine today?"
        )

        history.append({
            "role": "assistant",
            "text": reprompt,
        })

        db.upsert_session(
            sid,
            contact["id"],
            state="checkin",
            history=history,
        )

        gather = Gather(
=======
        reprompt = f"Hello {name}, I am here for your check-in. Did you take your reminders today, or how are you feeling?"
        history.append({"role": "assistant", "text": reprompt})
        db.upsert_session(sid, contact["id"], state="chat", history=history)
        g = Gather(
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845
            input="speech",
            action=action_url,
            method="POST",
            language=language,
            speech_timeout="auto",
            timeout=7,
        )

<<<<<<< HEAD
        gather.say(
            reprompt,
            voice="alice",
            language=language,
        )

        response.append(gather)

        return xml(response)

    # Generate conversational response.
    try:
        reply = generate_checkin_turn(
            contact,
            history,
            speech,
        )
    except Exception as exc:
        print(f"[conversation] Error: {exc}")

        reply = (
            "Thank you for telling me. "
            "I will record your response for review."
        )

    history.append({
        "role": "assistant",
        "text": reply,
    })

    db.upsert_session(
        sid,
        contact["id"],
        state="checkin",
        history=history,
    )

=======
    # Generate warm conversational response using LLM (Groq / Claude)
    reply = generate_checkin_turn(contact, history, speech)
    history.append({"role": "assistant", "text": reply})
    db.upsert_session(sid, contact["id"], state="chat", history=history)
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845
    if call_id:
        db.add_event(
            call_id,
            "CARECALL",
            reply,
        )

    # Finish after enough conversation turns.
    if len(history) >= 6:
        response.say(
            (
                f"{reply} Thank you for checking in, {name}. "
                "Have a good day. Goodbye."
            ),
            voice="alice",
            language=language,
        )

        response.hangup()

        background.add_task(
            evaluate_call_results,
            contact["id"],
            history,
        )

        if call_id:
            db.update_call(
                call_id,
                status="confirmed",
            )

            db.add_event(
                call_id,
                "TWILIO",
                "Check-in completed.",
            )

        return xml(response)

    gather = Gather(
        input="speech",
        action=action_url,
        method="POST",
        language=language,
        speech_timeout="auto",
        timeout=7,
    )

<<<<<<< HEAD
    gather.say(
        reply,
        voice="alice",
        language=language,
    )

    response.append(gather)

    return xml(response)

=======
>>>>>>> 1c488d6c2f765a4dbc121a3949ff3d1feb2b1845

# ============================================================
# TWILIO STATUS CALLBACK
# ============================================================

@app.api_route(
    "/twilio/status",
    methods=["GET", "POST"],
)
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

    status = str(
        form.get("CallStatus")
        or request.query_params.get("CallStatus")
        or "completed"
    )

    call_sid = str(
        form.get("CallSid")
        or request.query_params.get("CallSid")
        or ""
    )

    if contact_id:
        db.add_attempt(
            contact_id,
            attempt,
            call_sid,
            status,
        )

    call = db.get_call(call_id) if call_id else None

    contact_name = (
        call.get("sender_name")
        if call
        else None
    ) or "contact"

    contact_identifier = (
        contact_id
        or (call.get("id") if call else None)
    )

    if status in (
        "no-answer",
        "busy",
        "failed",
        "canceled",
    ):
        db.create_alert(
            contact_identifier or "unknown",
            "high",
            [
                f"Call to {contact_name} ended with "
                f"status {status}. Review the reminder."
            ],
        )

        if call_id:
            db.update_call(
                call_id,
                status="needs_attention",
                updated_at=now_iso(),
            )

            db.add_event(
                call_id,
                "SYSTEM",
                f"Call status: {status}.",
            )

    elif status == "completed":
        session = db.get_session(call_sid)

        history = (
            session.get("history", [])
            if session
            else []
        )

        if call_id and not history:
            events = db.list_events([call_id])

            history = [
                {
                    "role": (
                        "user"
                        if event.get("agent") == "CONTACT"
                        else "assistant"
                    ),
                    "text": event.get("text", ""),
                }
                for event in events
            ]

        if contact_identifier:
            background.add_task(
                evaluate_call_results,
                contact_identifier,
                history,
            )

        if call_id:
            db.update_call(
                call_id,
                status="confirmed",
                updated_at=now_iso(),
            )

            db.add_event(
                call_id,
                "TWILIO",
                "Call completed.",
            )

    return {
        "ok": True,
        "status": status,
    }