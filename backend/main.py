
"""CareCall backend: FastAPI app.

Run: uvicorn main:app --port 8000

/api/...      used by the dashboard
/twilio/...   webhooks for Twilio calls
/             dashboard served from ../frontend
"""

import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from twilio.twiml.voice_response import VoiceResponse

import config
import scheduler
from agents import (
    clamp_minutes,
    guardian_agent,
    listener_agent,
    planner_agent,
    reporter_agent,
)
from db import BACKEND_NAME, db, now_iso
from phrases import LANGS, lang_of, p
from telephony import gather_twiml, hangup_twiml


MAX_QUESTIONS = 2
MAX_UNCLEAR = 1


# ==========================================================================
# Application startup and shutdown
# ==========================================================================

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


def xml(twiml: str) -> Response:
    return Response(
        content=twiml,
        media_type="application/xml",
    )


# ==========================================================================
# Dashboard API
# ==========================================================================

class CallIn(BaseModel):
    phone: str
    message: str
    language: str = "en"
    scheduled_at: str
    note: str = ""
    sender_name: str = ""


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
    }


@app.post("/api/calls")
def create_call(body: CallIn):
    # Normalize the phone number.
    phone = re.sub(r"[\s\-()]", "", body.phone)

    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise HTTPException(
            status_code=400,
            detail="Phone must be in international format, e.g. +919876543210",
        )

    if not body.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message is empty",
        )

    language = body.language if body.language in LANGS else "en"

    try:
        when = datetime.fromisoformat(
            body.scheduled_at.replace("Z", "+00:00")
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="scheduled_at must be an ISO date-time",
        )

    if when.tzinfo is None:
        when = when.astimezone()

    # Do not schedule a call in the past.
    when = max(
        when,
        scheduler.utcnow() + timedelta(seconds=5),
    )

    # Ask the Planner agent to create a call plan.
    plan = planner_agent(
        body.message.strip(),
        language,
    )

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

    db.add_event(
        call_id,
        "PLANNER",
        (
            f"Priority {plan['priority'].upper()} "
            f"({plan['reason']}). Plan: up to "
            f"{plan['max_attempts']} attempts, "
            f"{plan['retry_gap_seconds']} s apart. "
            f"[{plan['source']}]"
        ),
    )

    if lang_of(row) != language:
        db.add_event(
            call_id,
            "SYSTEM",
            (
                f"{LANGS[language]['name']} is disabled; "
                "this call will use English."
            ),
        )

    scheduler.schedule_attempt(call_id, when)

    db.add_event(
        call_id,
        "SCHEDULER",
        f"Queued for {when.astimezone():%d %b %H:%M:%S}.",
    )

    return db.get_call(call_id)


@app.get("/api/calls")
def list_calls():
    calls = db.list_calls()

    events = db.list_events(
        [call["id"] for call in calls]
    )

    by_call = {}

    for event in events:
        by_call.setdefault(
            event["call_id"], []
        ).append(event)

    for call in calls:
        call["events"] = by_call.get(
            call["id"], []
        )

    return calls


@app.post("/api/calls/{call_id}/call-now")
def call_now(call_id: str):
    call = db.get_call(call_id)

    if not call:
        raise HTTPException(
            status_code=404,
            detail="Not found",
        )

    if call["status"] not in (
        "scheduled",
        "rescheduled",
        "retrying",
    ):
        db.update_call(
            call_id,
            status="scheduled",
            failed_attempts=0,
        )

    scheduler.schedule_attempt(
        call_id,
        scheduler.utcnow(),
    )

    db.add_event(
        call_id,
        "SCHEDULER",
        "Manual 'call now'.",
    )

    return {"ok": True}


@app.delete("/api/calls")
def reset_all():
    scheduler.cancel_all()
    db.reset()

    return {"ok": True}


# ==========================================================================
# Twilio webhook helpers
# ==========================================================================

def _url(path: str, **params) -> str:
    return f"{path}?{urlencode(params)}"


def _listen(
    call: dict,
    lang: str,
    texts: list,
    attempt: int,
    q: int,
    u: int,
) -> Response:

    params = {
        "call_id": call["id"],
        "attempt": attempt,
        "q": q,
        "u": u,
    }

    return xml(
        gather_twiml(
            lang,
            texts,
            _url("/twilio/gather", **params),
            _url("/twilio/noinput", **params),
        )
    )


# ==========================================================================
# Twilio voice webhook
# ==========================================================================

@app.post("/twilio/voice")
async def twilio_voice(
    call_id: str,
    attempt: int = 1,
):
    """Person answered: speak the reminder and ask for confirmation."""

    call = db.get_call(call_id)

    if not call:
        return xml(
            hangup_twiml(
                "en",
                ["Sorry, this reminder no longer exists. Goodbye."],
            )
        )

    lang = lang_of(call)

    sender = call.get("sender_name") or p(lang, "family")
    intro = p(lang, "intro", sender=sender)

    db.add_event(
        call_id,
        "TWILIO",
        "Call answered.",
    )

    db.add_event(
        call_id,
        "CARECALL",
        f"{intro} {call['spoken_message']}",
    )

    return _listen(
        call,
        lang,
        [
            intro,
            call["spoken_message"],
            p(lang, "ask"),
        ],
        attempt,
        0,
        0,
    )


@app.post("/twilio/gather")
async def twilio_gather(
    request: Request,
    call_id: str,
    attempt: int = 1,
    q: int = 0,
    u: int = 0,
):
    """Person replied: Listener decides and CareCall acts."""

    form = await request.form()

    speech = str(form.get("SpeechResult") or "")
    digits = str(form.get("Digits") or "")

    call = db.get_call(call_id)

    if not call:
        return xml(
            hangup_twiml("en", ["Goodbye."])
        )

    lang = lang_of(call)

    db.add_event(
        call_id,
        "PERSON",
        f"pressed {digits}" if digits else f'"{speech}"',
    )

    result = listener_agent(
        call,
        lang,
        speech,
        digits,
    )

    detail = (
        f" {clamp_minutes(result['minutes'])} min"
        if result["outcome"] == "LATER"
        else ""
    )

    db.add_event(
        call_id,
        "LISTENER",
        (
            f"{result['outcome']}{detail} "
            f"[{result['source']}]"
        ),
    )

    outcome = result["outcome"]

    # Person confirms that the reminder is completed.
    if outcome == "DONE":
        db.update_call(
            call_id,
            status="confirmed",
            next_attempt_at=None,
        )

        db.add_event(
            call_id,
            "CARECALL",
            p(lang, "done"),
        )

        return xml(
            hangup_twiml(
                lang,
                [p(lang, "done")],
            )
        )

    # Person asks to be reminded later.
    if outcome == "LATER":
        minutes = clamp_minutes(
            result["minutes"]
        )

        scheduler.reschedule(
            call_id,
            minutes,
        )

        text = p(
            lang,
            "later",
            m=minutes,
        )

        db.add_event(
            call_id,
            "CARECALL",
            text,
        )

        return xml(
            hangup_twiml(
                lang,
                [text],
            )
        )

    # Person reports a worrying situation.
    if outcome == "WORRYING":
        db.update_call(
            call_id,
            status="needs_attention",
            next_attempt_at=None,
        )

        db.add_event(
            call_id,
            "SYSTEM",
            (
                "Marked NEEDS ATTENTION. "
                "No escalation; the sender will see this."
            ),
        )

        db.add_event(
            call_id,
            "CARECALL",
            p(lang, "worrying"),
        )

        return xml(
            hangup_twiml(
                lang,
                [p(lang, "worrying")],
            )
        )

    # Person asks a question.
    if outcome == "QUESTION" and q < MAX_QUESTIONS:
        verdict = guardian_agent(
            call,
            lang,
            speech,
            result["reply"],
        )

        if verdict["ok"]:
            db.add_event(
                call_id,
                "GUARDIAN",
                (
                    f"PASSED: {verdict['reason']} "
                    f"[{verdict['source']}]"
                ),
            )
        else:
            db.add_event(
                call_id,
                "GUARDIAN",
                (
                    f'BLOCKED "{result["reply"]}": '
                    f'{verdict["reason"]} '
                    f'[{verdict["source"]}]'
                ),
            )

        answer = verdict["final_answer"]

        db.add_event(
            call_id,
            "CARECALL",
            answer,
        )

        return _listen(
            call,
            lang,
            [
                answer,
                p(lang, "reask"),
            ],
            attempt,
            q + 1,
            u,
        )

    # Unclear reply or too many questions.
    reason = (
        "Unclear reply"
        if outcome == "UNCLEAR"
        else "Too many questions"
    )

    return _unclear(
        call,
        lang,
        attempt,
        q,
        u,
        reason,
    )


@app.post("/twilio/noinput")
async def twilio_noinput(
    call_id: str,
    attempt: int = 1,
    q: int = 0,
    u: int = 0,
):
    """Person did not reply."""

    call = db.get_call(call_id)

    if not call:
        return xml(
            hangup_twiml("en", ["Goodbye."])
        )

    db.add_event(
        call_id,
        "PERSON",
        "(silence)",
    )

    return _unclear(
        call,
        lang_of(call),
        attempt,
        q,
        u,
        "No reply",
    )


def _unclear(
    call: dict,
    lang: str,
    attempt: int,
    q: int,
    u: int,
    reason: str,
) -> Response:

    if u < MAX_UNCLEAR:
        db.add_event(
            call["id"],
            "CARECALL",
            p(lang, "unclear"),
        )

        return _listen(
            call,
            lang,
            [p(lang, "unclear")],
            attempt,
            q,
            u + 1,
        )

    db.add_event(
        call["id"],
        "CARECALL",
        p(lang, "bye_unclear"),
    )

    scheduler.handle_failure(
        call["id"],
        reason,
    )

    return xml(
        hangup_twiml(
            lang,
            [p(lang, "bye_unclear")],
        )
    )


# ==========================================================================
# Twilio call status webhook
# ==========================================================================

@app.post("/twilio/status")
async def twilio_status(
    request: Request,
    background: BackgroundTasks,
    call_id: str,
    attempt: int = 1,
):
    """Twilio reports the final status of a call."""

    form = await request.form()

    status = str(
        form.get("CallStatus") or "completed"
    )

    duration = str(
        form.get("CallDuration") or "0"
    )

    call = db.get_call(call_id)

    if not call:
        return {"ok": True}

    # Ignore callbacks from an older attempt.
    if int(call.get("attempts") or 0) != attempt:
        return {
            "ok": True,
            "ignored": "old attempt",
        }

    db.add_event(
        call_id,
        "TWILIO",
        f"Call ended: {status} ({duration} s).",
    )

    if call["status"] == "calling":
        reason = {
            "no-answer": "No answer",
            "busy": "Busy / declined",
            "failed": "Call failed",
            "canceled": "Call canceled",
        }.get(
            status,
            "Hung up without answering",
        )

        scheduler.handle_failure(
            call_id,
            reason,
        )

    background.add_task(
        run_reporter,
        call_id,
    )

    return {"ok": True}


# ==========================================================================
# Reporter agent
# ==========================================================================

def run_reporter(call_id: str):
    call = db.get_call(call_id)

    if not call:
        return

    summary = reporter_agent(
        call,
        db.list_events([call_id]),
    )

    db.update_call(
        call_id,
        summary=summary,
    )

    db.add_event(
        call_id,
        "REPORTER",
        summary,
    )


# ==========================================================================
# Dashboard frontend
# Keep this at the bottom of the file.
# ==========================================================================

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

if not FRONTEND_DIR.exists():
    raise RuntimeError(
        f"Frontend folder not found: {FRONTEND_DIR}"
    )

app.mount(
    "/",
    StaticFiles(
        directory=str(FRONTEND_DIR),
        html=True,
    ),
    name="frontend",
)
