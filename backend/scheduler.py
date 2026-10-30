"""APScheduler inside the backend: fires calls, reschedules, retries, restores after restart."""
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

import telephony
from db import PENDING, db

sched = BackgroundScheduler(timezone="UTC")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def start():
    if not sched.running:
        sched.start()
    restore_pending()


def shutdown():
    if sched.running:
        sched.shutdown(wait=False)


def schedule_attempt(call_id: str, when: datetime):
    when = max(when, utcnow() + timedelta(seconds=2))
    sched.add_job(fire_call, DateTrigger(run_date=when.astimezone(timezone.utc), timezone=timezone.utc), args=[call_id], id=f"call-{call_id}",
                  replace_existing=True, misfire_grace_time=600)


def cancel_all():
    sched.remove_all_jobs()


def restore_pending():
    """After a restart, put every pending call back on the scheduler."""
    for c in db.list_pending():
        when = parse_dt(c.get("next_attempt_at") or c["scheduled_at"])
        if when < utcnow():
            when = utcnow() + timedelta(seconds=5)
        schedule_attempt(c["id"], when)
        db.add_event(c["id"], "SCHEDULER", f"Restored after restart; will call at {when.astimezone():%H:%M:%S}.")
        print(f"[scheduler] restored call {c['id']} for {when.isoformat()}")


def fire_call(call_id: str):
    """Runs at the scheduled time: places the call through Twilio."""
    c = db.get_call(call_id)
    if not c or c["status"] not in PENDING:
        return
    attempt = int(c.get("attempts") or 0) + 1
    db.update_call(call_id, status="calling", attempts=attempt)
    db.add_event(call_id, "SCHEDULER", f"Attempt {attempt} of plan: calling {c['phone']}.")
    try:
        sid = telephony.place_call(c, attempt)
        db.update_call(call_id, twilio_sid=sid)
        db.add_event(call_id, "TWILIO", f"Ringing (call {sid}).")
    except Exception as e:
        db.add_event(call_id, "SYSTEM", f"Could not start call: {str(e)[:150]}")
        handle_failure(call_id, "call could not be placed")


def handle_failure(call_id: str, reason: str):
    """No answer / unclear / hung up: retry by the Planner's plan, or mark unreachable."""
    c = db.get_call(call_id)
    if not c:
        return
    failed = int(c.get("failed_attempts") or 0) + 1
    max_attempts = int(c.get("max_attempts") or 2)
    if failed >= max_attempts:
        db.update_call(call_id, status="unreachable", failed_attempts=failed, next_attempt_at=None)
        db.add_event(call_id, "SCHEDULER", f"{reason}. No success after {failed} attempt(s): marked UNREACHABLE.")
        return
    gap = int(c.get("retry_gap_seconds") or 600)
    when = utcnow() + timedelta(seconds=gap)
    db.update_call(call_id, status="retrying", failed_attempts=failed, next_attempt_at=when.isoformat())
    schedule_attempt(call_id, when)
    db.add_event(call_id, "SCHEDULER",
                 f"{reason}. Retry {failed + 1} of {max_attempts} in {gap} s ({c['priority']} priority plan).")


def reschedule(call_id: str, minutes: int):
    when = utcnow() + timedelta(minutes=minutes)
    db.update_call(call_id, status="rescheduled", next_attempt_at=when.isoformat())
    schedule_attempt(call_id, when)
    db.add_event(call_id, "SCHEDULER", f"Rescheduled: will call again in {minutes} min (at {when.astimezone():%H:%M}).")
