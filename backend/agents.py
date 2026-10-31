
import re

import config
from llm import ask_json
from phrases import LANGS, p


# --------------------------------------------------
# Retry plans
# --------------------------------------------------

PLANS = {
    "critical": {
        "max_attempts": 3,
        "retry_gap_seconds": 120,
    },
    "high": {
        "max_attempts": 3,
        "retry_gap_seconds": 300,
    },
    "normal": {
        "max_attempts": 2,
        "retry_gap_seconds": 600,
    },
}

DEMO_GAP_SECONDS = 20


def plan_for(priority: str) -> dict:
    """Return retry settings for the selected priority."""

    priority = (priority or "normal").lower()
    plan = dict(PLANS.get(priority, PLANS["normal"]))

    if config.DEMO_MODE:
        plan["retry_gap_seconds"] = DEMO_GAP_SECONDS

    return plan


# --------------------------------------------------
# Keyword helpers
# --------------------------------------------------

CRITICAL_KEYWORDS = (
    "urgent",
    "immediately",
    "emergency",
    "critical",
)

HIGH_KEYWORDS = (
    "appointment",
    "important",
    "today",
    "tomorrow",
)

WORRYING_KEYWORDS = (
    "unwell",
    "feeling sick",
    "feel sick",
    "dizzy",
    "dizziness",
    "chest pain",
    "difficulty breathing",
    "can't breathe",
    "cannot breathe",
    "allergic reaction",
    "severe pain",
)

DONE_KEYWORDS = (
    "already taken",
    "taken my medicine",
    "took my medicine",
    "already took",
    "done",
)

LATER_KEYWORDS = (
    "later",
    "in a while",
    "in 10 minutes",
    "in ten minutes",
    "after",
    "not now",
    "remind me",
)

QUESTION_KEYWORDS = (
    "what medicine",
    "which medicine",
    "what dose",
    "how much",
    "should i take",
    "can i take",
    "what time",
)


def keyword_outcome(speech: str) -> str:
    """Classify speech using conservative keyword rules."""

    text = (speech or "").lower().strip()

    if not text:
        return "UNCLEAR"

    # Safety-related concerns take priority.
    if any(word in text for word in WORRYING_KEYWORDS):
        return "WORRYING"

    if any(word in text for word in DONE_KEYWORDS):
        return "DONE"

    if any(word in text for word in LATER_KEYWORDS):
        return "LATER"

    if any(word in text for word in QUESTION_KEYWORDS):
        return "QUESTION"

    return "UNCLEAR"


def extract_minutes(text: str):
    """Extract a delay in minutes from speech."""

    text = (text or "").lower()

    patterns = [
        r"(\d+)\s*minutes?",
        r"(\d+)\s*mins?",
        r"(\d+)\s*hours?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            value = int(match.group(1))

            if "hour" in pattern:
                value *= 60

            return value

    if "ten minutes" in text:
        return 10

    if "five minutes" in text:
        return 5

    if "half an hour" in text:
        return 30

    return None


def clamp_minutes(minutes):
    """Keep reminder delays within reasonable bounds."""

    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return 10

    return max(1, min(minutes, 1440))


# --------------------------------------------------
# Planner agent
# --------------------------------------------------

PLANNER_SYSTEM = """
You are the CareCall reminder-planning agent.

Choose a priority for the reminder.

Return one JSON object with:
- priority: critical, high, or normal
- reason: a short explanation

Use critical only when the sender's message clearly indicates urgency.
Use high for important time-sensitive reminders.
Otherwise use normal.

Do not provide medical advice.
Do not invent information.
"""


def planner_agent(call: dict) -> dict:
    """Determine reminder priority."""

    message = str(call.get("message") or "").strip()
    note = str(call.get("note") or "").strip()

    user = (
        f"Reminder message: {message}\n"
        f"Sender's note: {note}"
    )

    try:
        result = ask_json(
            PLANNER_SYSTEM,
            user,
            max_tokens=250,
            timeout=8,
        )

        priority = str(
            result.get("priority", "normal")
        ).lower()

        if priority not in ("critical", "high", "normal"):
            priority = "normal"

        return {
            "priority": priority,
            "reason": str(result.get("reason", "")),
            "source": "llm",
        }

    except Exception:
        text = f"{message} {note}".lower()

        if any(word in text for word in CRITICAL_KEYWORDS):
            priority = "critical"
        elif any(word in text for word in HIGH_KEYWORDS):
            priority = "high"
        else:
            priority = "normal"

        return {
            "priority": priority,
            "reason": "Keyword-based fallback",
            "source": "keyword fallback",
        }


# --------------------------------------------------
# Listener agent
# --------------------------------------------------

LISTENER_SYSTEM = """
You are the CareCall Listener agent.

Classify the person's response into exactly one outcome:
DONE, LATER, QUESTION, WORRYING, or UNCLEAR.

Return one JSON object containing:
- outcome: one allowed outcome
- minutes: a number of minutes if the person requests a delay,
  otherwise null
- reply: a short, polite response in the requested language

Rules:
1. DONE means the person confirms they have taken the medicine.
2. LATER means the person asks to take it later or requests a delay.
3. QUESTION means the person asks a question.
4. WORRYING means the person reports feeling unwell or raises a
   possible health or safety concern.
5. UNCLEAR means the response cannot be classified reliably.

Never recommend changing a dose, stopping medication, starting
another medicine, or making a treatment decision.
Never invent medical facts.
For WORRYING, acknowledge the person's concern without giving
medical advice. The application should flag this outcome for the sender.
For QUESTION, do not guess. Say that the information is unavailable
and that the sender will be informed.
For LATER, acknowledge the requested delay.
Reply in the requested language.
"""


def listener_agent(
    call: dict,
    lang: str,
    speech: str,
    digits: str,
) -> dict:
    """Classify a contact's response with safe fallback handling."""

    digits = (digits or "").strip()

    # Keypad response handling.
    if digits:
        mapping = {
            "1": "DONE",
            "2": "LATER",
            "3": "WORRYING",
        }

        outcome = mapping.get(digits[0], "UNCLEAR")

        return {
            "outcome": outcome,
            "minutes": 10 if outcome == "LATER" else None,
            "reply": "",
            "source": "keypad",
        }

    speech = (speech or "").strip()

    if not speech:
        return {
            "outcome": "UNCLEAR",
            "minutes": None,
            "reply": p(lang, "noinfo"),
            "source": "empty",
        }

    note = call.get("note") or call.get("message", "")
    message = call.get("message", "")

    user = (
        f"Call language: {LANGS.get(lang, LANGS['en'])['name']}\n"
        f"Reminder: {message}\n"
        f"Sender's note: {note}\n"
        f'Person said: "{speech}"'
    )

    # Deterministic safety classification before the LLM.
    rule_outcome = keyword_outcome(speech)

    try:
        result = ask_json(
            LISTENER_SYSTEM,
            user,
            max_tokens=250,
        )

        outcome = str(
            result.get("outcome", "UNCLEAR")
        ).upper()

        allowed_outcomes = {
            "DONE",
            "LATER",
            "QUESTION",
            "WORRYING",
            "UNCLEAR",
        }

        if outcome not in allowed_outcomes:
            outcome = "UNCLEAR"

        # Safety-related keyword matches override the LLM.
        if rule_outcome == "WORRYING":
            outcome = "WORRYING"

        # Use the keyword result for clear simple responses
        # when the LLM gives an uncertain classification.
        if outcome == "UNCLEAR" and rule_outcome in (
            "DONE",
            "LATER",
            "QUESTION",
        ):
            outcome = rule_outcome

        minutes = extract_minutes(speech)

        if minutes is None:
            minutes = result.get("minutes")

        if outcome == "LATER":
            minutes = clamp_minutes(minutes)
        else:
            minutes = None

        reply = str(result.get("reply") or "").strip()

        if not reply or "NOINFO" in reply.upper():
            reply = p(lang, "noinfo")

        # Do not speak model-generated medical advice in a
        # worrying situation. Use a neutral, localized fallback.
        if outcome == "WORRYING":
            reply = p(lang, "noinfo")

        return {
            "outcome": outcome,
            "minutes": minutes,
            "reply": reply,
            "source": "llm",
        }

    except Exception as error:
        outcome = rule_outcome
        minutes = extract_minutes(speech)

        if outcome == "LATER":
            minutes = clamp_minutes(minutes)
        else:
            minutes = None

        # Use an existing localized fallback rather than
        # assuming that other translation keys exist.
        reply = p(lang, "noinfo")

        return {
            "outcome": outcome,
            "minutes": minutes,
            "reply": reply,
            "source": (
                f"keyword fallback ({type(error).__name__})"
            ),
        }


# --------------------------------------------------
# Guardian agent
# --------------------------------------------------

GUARDIAN_SYSTEM = """
You are the CareCall safety Guardian.

Check whether a proposed answer is directly supported by the sender's note.

Return one JSON object with:
- ok: a Boolean
- reason: a short explanation

Approve only if the answer is supported by the note.
Reject unsupported claims, invented facts, and medical advice.
Reject advice to change doses, stop medication, or start medication.
If uncertain, reject the answer.
"""


def guardian_agent(
    call: dict,
    lang: str,
    question: str,
    answer: str,
) -> dict:
    """Reject unsupported or unsafe proposed answers."""

    safe = p(lang, "noinfo")

    if not answer or not answer.strip():
        return {
            "ok": False,
            "final_answer": safe,
            "reason": "Empty proposed answer",
            "source": "rule",
        }

    note = str(
        call.get("note") or call.get("message") or ""
    )

    # Conservative rejection of common dangerous advice.
    dangerous_patterns = (
        r"\bdouble your dose\b",
        r"\bdouble the dose\b",
        r"\bstop taking\b",
        r"\bstop your medication\b",
        r"\btake extra tablets\b",
        r"\bincrease your dose\b",
        r"\bdecrease your dose\b",
        r"\bskip your medication\b",
    )

    for pattern in dangerous_patterns:
        if re.search(pattern, answer, flags=re.IGNORECASE):
            return {
                "ok": False,
                "final_answer": safe,
                "reason": "Potentially unsafe medical advice",
                "source": "rule",
            }

    # A proposed answer cannot add facts when the note is empty.
    if not note.strip():
        return {
            "ok": False,
            "final_answer": safe,
            "reason": "No supporting sender note",
            "source": "rule",
        }

    user = (
        f"Sender's note: {note}\n"
        f"Person's question: {question}\n"
        f"Proposed answer: {answer}\n\n"
        "Approve only if the proposed answer is supported by the note. "
        "Reject unsupported medical claims or advice."
    )

    try:
        result = ask_json(
            GUARDIAN_SYSTEM,
            user,
            max_tokens=180,
            timeout=5.0,
        )

        ok = result.get("ok") is True

        return {
            "ok": ok,
            "final_answer": answer if ok else safe,
            "reason": str(result.get("reason", ""))[:100],
            "source": "llm",
        }

    except Exception as error:
        return {
            "ok": False,
            "final_answer": safe,
            "reason": (
                f"Could not verify ({type(error).__name__})"
            ),
            "source": "fallback",
        }


# --------------------------------------------------
# Reporter agent
# --------------------------------------------------

REPORTER_SYSTEM = """
You are the CareCall reporting agent.

Summarize a reminder interaction for the sender.

Return one JSON object with:
- summary: a short factual summary
- needs_attention: a Boolean

Do not invent facts.
Set needs_attention to true when the response indicates a concern,
a safety issue, or an unresolved problem.
Keep the summary concise.
"""


def _fallback_summary(outcome: str, speech: str) -> str:
    """Create a short factual summary without inventing details."""

    summaries = {
        "DONE": "The person confirmed taking the medicine.",
        "LATER": "The person requested a later reminder.",
        "QUESTION": "The person asked a question.",
        "WORRYING": "ATTENTION: The person reported a concern.",
        "UNCLEAR": "The response could not be understood.",
    }

    summary = summaries.get(
        outcome,
        "The interaction could not be summarized.",
    )

    if speech and outcome in ("QUESTION", "WORRYING", "UNCLEAR"):
        summary += f" Response: {speech[:100]}"

    return summary[:300]


def reporter_agent(
    call: dict,
    outcome: str,
    speech: str,
) -> dict:
    """Summarize the interaction and flag concerns."""

    outcome = (outcome or "UNCLEAR").upper()
    speech = (speech or "").strip()

    # Never depend on the LLM to flag a known worrying outcome.
    if outcome == "WORRYING":
        return {
            "summary": _fallback_summary(outcome, speech),
            "needs_attention": True,
            "source": "rule",
        }

    user = (
        f"Reminder: {call.get('message', '')}\n"
        f"Sender's note: {call.get('note', '')}\n"
        f"Outcome: {outcome}\n"
        f"Person's response: {speech}"
    )

    try:
        result = ask_json(
            REPORTER_SYSTEM,
            user,
            max_tokens=150,
            timeout=8,
        )

        summary = str(result.get("summary", "")).strip()

        if not summary:
            summary = _fallback_summary(outcome, speech)

        needs_attention = result.get("needs_attention") is True

        return {
            "summary": summary[:300],
            "needs_attention": needs_attention,
            "source": "llm",
        }

    except Exception:
        needs_attention = outcome in (
            "QUESTION",
            "WORRYING",
            "UNCLEAR",
        )

        return {
            "summary": _fallback_summary(outcome, speech),
            "needs_attention": needs_attention,
            "source": "fallback",
        }
