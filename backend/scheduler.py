
"""
CareCall scheduler.

Responsibilities:
- Schedule reminder calls.
- Restore pending reminders after a backend restart.
- Respect each reminder's configured attempt limit and retry interval.
- Retry unsuccessful calls.
- Mark reminders needing attention after the final failed attempt.
- Support rescheduling.

Keep DRY_RUN=true in config while testing.
"""

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

import telephony
from db import PENDING, db


sched = BackgroundScheduler(timezone="UTC")

MAX_ATTEMPTS = 5
RETRY_GAP_SECONDS = 180


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def get_max_attempts(call: dict) -> int:
    try:
        return max(1, int(call.get("max_attempts") or MAX_ATTEMPTS))
    except (TypeError, ValueError):
        return MAX_ATTEMPTS


def get_retry_gap(call: dict) -> int:
    try:
        return max(
            1,
            int(call.get("retry_gap_seconds") or RETRY_GAP_SECONDS),
        )
    except (TypeError, ValueError):
        return RETRY_GAP_SECONDS


def start():
    if not sched.running:
        sched.start()

    restore_pending()


def shutdown():
    if sched.running:
        sched.shutdown(wait=False)


def schedule_attempt(call_id: str, when: datetime):
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)

    when = max(
        when.astimezone(timezone.utc),
        utcnow() + timedelta(seconds=2),
    )

    sched.add_job(
        fire_call,
        trigger=DateTrigger(run_date=when, timezone=timezone.utc),
        args=[call_id],
        id=f"call-{call_id}",
        replace_existing=True,
        misfire_grace_time=600,
    )


def cancel_all():
    if sched.running:
        sched.remove_all_jobs()


def restore_pending():
    for call in db.list_pending():
        try:
            call_id = call["id"]
            scheduled_time = (
                call.get("next_attempt_at")
                or call.get("scheduled_at")
            )

            if not scheduled_time:
                print(f"[scheduler] no scheduled time for {call_id}")
                continue

            when = parse_dt(scheduled_time)

            if when < utcnow():
                when = utcnow() + timedelta(seconds=5)

            schedule_attempt(call_id, when)

            db.add_event(
                call_id,
                "SCHEDULER",
                (
                    "Restored after restart; scheduled for "
                    f"{when.strftime('%H:%M:%S')} UTC."
                ),
            )

            print(
                f"[scheduler] restored {call_id} "
                f"for {when.isoformat()}"
            )

        except Exception as exc:
            print(
                "[scheduler] could not restore reminder "
                f"{call.get('id', 'unknown')}: {exc}"
            )


def fire_call(call_id: str):
    call = db.get_call(call_id)

    if not call:
        return

    if call.get("status") not in PENDING:
        return

    max_attempts = get_max_attempts(call)
    attempts_so_far = int(call.get("attempts") or 0)

    if attempts_so_far >= max_attempts:
        db.update_call(
            call_id,
            status="needs_attention",
            next_attempt_at=None,
            summary=(
                "ATTENTION: The reminder reached its configured "
                "maximum number of call attempts."
            ),
        )

        db.add_event(
            call_id,
            "SCHEDULER",
            (
                f"Maximum of {max_attempts} attempts reached. "
                "No further call will be placed."
            ),
        )
        return

    attempt = attempts_so_far + 1

    # Claim the pending reminder using a conditional update.
    # Only one worker can move it from its previous status to calling.
    old_status = call.get("status")

    claimed = db.update_call_if_status(
        call_id,
        old_status,
        status="calling",
        attempts=attempt,
        next_attempt_at=None,
    )

    if not claimed:
        print(
            f"[scheduler] skipped duplicate attempt for {call_id}; "
            "the status changed before it could be claimed."
        )
        return

    db.add_event(
        call_id,
        "SCHEDULER",
        f"Attempt {attempt} of {max_attempts}: starting reminder call.",
    )

    # Fetch the current record after claiming it.
    call = db.get_call(call_id)

    if not call:
        return

    try:
        sid = telephony.place_call(call, attempt)

        db.update_call(
            call_id,
            twilio_sid=sid,
        )

        db.add_event(
            call_id,
            "TWILIO",
            f"Call initiated or simulated (call ID {sid}).",
        )

    except Exception as exc:
        db.add_event(
            call_id,
            "SYSTEM",
            f"Could not start call: {str(exc)[:150]}",
        )

        handle_failure(call_id, "Call could not be placed")


def handle_failure(call_id: str, reason: str):
    """
    Process a failure only if the reminder is still calling.

    The conditional database update prevents duplicate failure callbacks
    from independently scheduling multiple retries.
    """
    call = db.get_call(call_id)

    if not call:
        return

    if call.get("status") != "calling":
        print(
            f"[scheduler] ignored stale failure for {call_id}; "
            f"current status is {call.get('status')}."
        )
        return

    attempts = int(call.get("attempts") or 0)
    failed_attempts = int(call.get("failed_attempts") or 0) + 1

    max_attempts = get_max_attempts(call)
    retry_gap = get_retry_gap(call)

    if attempts >= max_attempts:
        updated = db.update_call_if_status(
            call_id,
            "calling",
            status="needs_attention",
            failed_attempts=failed_attempts,
            next_attempt_at=None,
            summary=(
                "ATTENTION: The reminder was unsuccessful after "
                f"{attempts} call attempt(s). Please review it."
            ),
        )

        if not updated:
            print(
                f"[scheduler] another handler already processed "
                f"failure for {call_id}."
            )
            return

        db.add_event(
            call_id,
            "SCHEDULER",
            (
                f"{reason}. Maximum of {max_attempts} attempts "
                "reached. Marked needs_attention."
            ),
        )
        return

    next_attempt = utcnow() + timedelta(seconds=retry_gap)

    updated = db.update_call_if_status(
        call_id,
        "calling",
        status="retrying",
        failed_attempts=failed_attempts,
        next_attempt_at=next_attempt.isoformat(),
        summary=(
            f"No successful confirmation after {attempts} "
            f"attempt(s); retrying in {retry_gap} seconds."
        ),
    )

    if not updated:
        print(
            f"[scheduler] another handler already processed "
            f"failure for {call_id}."
        )
        return

    # Schedule only after the conditional database update succeeds.
    schedule_attempt(call_id, next_attempt)

    db.add_event(
        call_id,
        "SCHEDULER",
        (
            f"{reason}. Attempt {attempt} failed; "
            f"next attempt {attempts + 1} of {max_attempts} "
            f"in {retry_gap} seconds."
        ).replace(
            f"Attempt {attempt} failed",
            f"Attempt {attempts} failed",
        ),
    )


def reschedule(call_id: str, minutes: int):
    if minutes < 1:
        raise ValueError("Reschedule minutes must be at least 1.")

    call = db.get_call(call_id)

    if not call:
        raise ValueError(f"Call {call_id} was not found.")

    when = utcnow() + timedelta(minutes=minutes)

    db.update_call(
        call_id,
        status="rescheduled",
        next_attempt_at=when.isoformat(),
    )

    schedule_attempt(call_id, when)

    db.add_event(
        call_id,
        "SCHEDULER",
        (
            f"Rescheduled: another call is planned in {minutes} "
            f"minute(s), at {when.strftime('%H:%M:%S')} UTC."
        ),
    )
