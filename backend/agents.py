"""The four AI agents. Each is a plain Python function with its own short prompt.

Planner  -> runs when a reminder is scheduled: priority + retry plan (+ translation)
Listener -> runs live on the call: DONE / LATER / QUESTION / WORRYING / UNCLEAR
Guardian -> checks an answer to a question before it is spoken
Reporter -> one sentence (max 25 words) for the sender after the call

Every LLM call is wrapped in try/except with a non-AI fallback.
"""
import re

import config
from llm import ask_json
from phrases import LANGS, p

# --------------------------------------------------------------------------
# Retry plans chosen by priority
# --------------------------------------------------------------------------
PLANS = {
    "critical": {"max_attempts": 3, "retry_gap_seconds": 120},
    "high": {"max_attempts": 3, "retry_gap_seconds": 300},
    "normal": {"max_attempts": 2, "retry_gap_seconds": 600},
}
DEMO_GAP_SECONDS = 20


def plan_for(priority: str) -> dict:
    plan = dict(PLANS.get(priority, PLANS["normal"]))
    if config.DEMO_MODE:
        plan["retry_gap_seconds"] = DEMO_GAP_SECONDS
    return plan


# --------------------------------------------------------------------------
# Keyword lists (used when the LLM is slow, down, or not needed)
# --------------------------------------------------------------------------
KW = {
    "WORRYING": ["help", "pain", "hurt", "fell", "fall", "fallen", "dizzy", "sick", "unwell", "emergency",
                 "chest", "breathe", "breathing", "ambulance", "bleeding", "not well", "not feeling well",
                 "मदद", "दर्द", "गिर", "चक्कर", "तबीयत", "बीमार", "madad", "dard", "chakkar",
                 "ಸಹಾಯ", "ನೋವು", "ಬಿದ್ದೆ", "ತಲೆ ಸುತ್ತು", "ಹುಷಾರಿಲ್ಲ", "sahaya", "novu"],
    "LATER": ["later", "call back", "call me back", "not now", "busy", "not yet", "minute", "minutes",
              "hour", "remind me", "बाद में", "बाद", "थोड़ी देर", "मिनट", "अभी नहीं", "baad", "baad mein",
              "ನಂತರ", "ಆಮೇಲೆ", "ನಿಮಿಷ", "amele", "nantara"],
    "DONE": ["done", "yes", "yeah", "yep", "took", "taken", "finished", "completed", "already", "did it",
             "हाँ", "हां", "हो गया", "ले ली", "ले लिया", "कर लिया", "खा ली", "haan", "ho gaya", "le li", "kar liya",
             "ಹೌದು", "ಆಯ್ತು", "ಆಯಿತು", "ಮಾಡಿದೆ", "ತಗೊಂಡೆ", "haudu", "aaytu", "maadide"],
    "QUESTION": ["?", "what", "which", "when", "where", "how", "why", "can i", "should i", "who",
                 "क्या", "कौन", "कब", "कैसे", "कहाँ", "kya", "kaun", "kab", "kaise",
                 "ಯಾವ", "ಏನು", "ಯಾವಾಗ", "ಹೇಗೆ", "ಎಲ್ಲಿ", "yaava", "enu"],
}
CRITICAL_WORDS = ["medicine", "medication", "tablet", "pill", "insulin", "dose", "bp", "blood pressure", "sugar",
                  "inhaler", "injection", "health", "dawai", "दवा", "दवाई", "गोली", "ಔಷಧ", "ಮಾತ್ರೆ"]
HIGH_WORDS = ["appointment", "doctor", "hospital", "clinic", "checkup", "check-up", "test", "scan", "bank",
              "डॉक्टर", "अस्पताल", "ಡಾಕ್ಟರ್", "ಆಸ್ಪತ್ರೆ"]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45}


def _has(text: str, word: str) -> bool:
    if word.isascii() and word[0].isalnum():
        return re.search(r"\b" + re.escape(word) + r"\b", text) is not None
    return word in text


def keyword_outcome(text: str) -> str:
    t = text.lower().strip()
    if not t:
        return "UNCLEAR"
    for outcome in ("WORRYING", "LATER", "DONE", "QUESTION"):
        if any(_has(t, w) for w in KW[outcome]):
            return outcome
    return "UNCLEAR"


def extract_minutes(text: str):
    t = text.lower()
    m = re.search(r"(\d+)\s*(min|minute|minutes|मिनट|ನಿಮಿಷ)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(hour|hours|घंटे|घंटा|ಗಂಟೆ)", t)
    if m:
        return int(m.group(1)) * 60
    if "half an hour" in t or "आधा घंटा" in t or "ಅರ್ಧ ಗಂಟೆ" in t:
        return 30
    for word, n in sorted(NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{word}\s+minutes?\b", t):
            return n
    if re.search(r"\b(an|one) hour\b", t) or "एक घंटा" in t:
        return 60
    return None


def clamp_minutes(m) -> int:
    try:
        m = int(m)
    except Exception:
        m = 10
    return max(1, min(m, 240))


# --------------------------------------------------------------------------
# 1. Planner agent
# --------------------------------------------------------------------------
PLANNER_SYSTEM = (
    "You are the Planner agent of CareCall, a check-in phone-call service for contacts. "
    "Decide the priority of a reminder: critical = medicine or health, high = appointments, normal = everything else. "
    'Return JSON: {"priority":"critical|high|normal","reason":"max 8 words","spoken_message":"..."}. '
    "spoken_message = the reminder rewritten for speaking, in the target language, same meaning, nothing added."
)


def planner_agent(message: str, language: str) -> dict:
    lang_name = LANGS.get(language, LANGS["en"])["name"]
    try:
        out = ask_json(PLANNER_SYSTEM, f"Target language: {lang_name}\nReminder: {message}", max_tokens=250, timeout=8)
        priority = str(out.get("priority", "normal")).lower()
        if priority not in PLANS:
            priority = "normal"
        spoken = str(out.get("spoken_message") or message).strip()
        if language == "en":
            spoken = message  # keep the sender's exact words in English
        return {"priority": priority, "reason": str(out.get("reason", ""))[:80], "spoken_message": spoken,
                "source": "llm", **plan_for(priority)}
    except Exception as e:
        t = message.lower()
        if any(_has(t, w) for w in CRITICAL_WORDS):
            priority = "critical"
        elif any(_has(t, w) for w in HIGH_WORDS):
            priority = "high"
        else:
            priority = "normal"
        return {"priority": priority, "reason": "keyword rule", "spoken_message": message,
                "source": f"fallback ({type(e).__name__})", **plan_for(priority)}


# --------------------------------------------------------------------------
# 2. Listener agent
# --------------------------------------------------------------------------
LISTENER_SYSTEM = (
    "You are the Listener agent on a reminder phone call. Classify what the person said into exactly one outcome: "
    "DONE (they did it / yes), LATER (call back later; give minutes, default 10), "
    "QUESTION (they asked something), WORRYING (pain, fall, unwell, confused, needs help, emergency), "
    "UNCLEAR (anything else). For QUESTION, reply = a short answer using ONLY facts in the sender's note; "
    "if the note does not contain the answer, reply exactly: NOINFO. Never give medical advice. "
    'Return JSON: {"outcome":"DONE|LATER|QUESTION|WORRYING|UNCLEAR","minutes":null,"reply":"one short sentence"}. '
    "Write reply in the call language."
)


def listener_agent(call: dict, lang: str, speech: str, digits: str) -> dict:
    """Returns {outcome, minutes, reply, source}."""
    digits = (digits or "").strip()
    if digits:
        mapping = {"1": "DONE", "2": "LATER", "3": "WORRYING"}
        return {"outcome": mapping.get(digits[0], "UNCLEAR"), "minutes": 10, "reply": "", "source": "keypad"}

    speech = (speech or "").strip()
    if not speech:
        return {"outcome": "UNCLEAR", "minutes": None, "reply": "", "source": "empty"}

    note = call.get("note") or call.get("message")
    user = (f"Call language: {LANGS[lang]['name']}\nReminder: {call['message']}\n"
            f"Sender's note: {note}\nPerson said: \"{speech}\"")
    try:
        out = ask_json(LISTENER_SYSTEM, user, max_tokens=120)
        outcome = str(out.get("outcome", "UNCLEAR")).upper()
        if outcome not in ("DONE", "LATER", "QUESTION", "WORRYING", "UNCLEAR"):
            outcome = "UNCLEAR"
        # safety net: never let the LLM miss a worrying word
        if keyword_outcome(speech) == "WORRYING":
            outcome = "WORRYING"
        reply = str(out.get("reply") or "").strip()
        if "NOINFO" in reply.upper() or not reply:
            reply = p(lang, "noinfo")
        minutes = extract_minutes(speech) or out.get("minutes")
        return {"outcome": outcome, "minutes": minutes, "reply": reply, "source": "llm"}
    except Exception as e:
        outcome = keyword_outcome(speech)
        return {"outcome": outcome, "minutes": extract_minutes(speech), "reply": p(lang, "noinfo"),
                "source": f"keyword fallback ({type(e).__name__})"}


# --------------------------------------------------------------------------
# 3. Guardian agent
# --------------------------------------------------------------------------
GUARDIAN_SYSTEM = (
    "You are the Guardian safety agent. Check a proposed answer that will be spoken to a contact. "
    "Approve ONLY if: every fact in it appears in the sender's note, it gives no medical advice "
    "beyond repeating the note, and it invents nothing. "
    'Return JSON: {"ok":true|false,"reason":"max 10 words"}.'
)


def guardian_agent(call: dict, lang: str, question: str, answer: str) -> dict:
    """Returns {ok, final_answer, reason, source}."""
    safe = p(lang, "noinfo")
    if answer.strip() == safe:
        return {"ok": True, "final_answer": safe, "reason": "safe 'no information' reply", "source": "rule"}
    note = call.get("note") or call.get("message")
    try:
        out = ask_json(GUARDIAN_SYSTEM, f"Sender's note: {note}\nQuestion: {question}\nProposed answer: {answer}",
                       max_tokens=60, timeout=2.0)
        ok = out.get("ok") is True or str(out.get("ok")).lower() == "true"
        return {"ok": ok, "final_answer": answer if ok else safe, "reason": str(out.get("reason", ""))[:100],
                "source": "llm"}
    except Exception as e:
        # fail closed: if we cannot check it, we do not say it
        return {"ok": False, "final_answer": safe, "reason": f"could not verify ({type(e).__name__})",
                "source": "fallback"}


# --------------------------------------------------------------------------
# 4. Reporter agent
# --------------------------------------------------------------------------
REPORTER_SYSTEM = (
    "You are the Reporter agent. Write ONE plain sentence (max 25 words) for the family member who sent a reminder, "
    "saying what happened on the call. Use only the transcript. If status is needs_attention start with 'ATTENTION:'. "
    'Return JSON: {"summary":"..."}'
)


def _fallback_summary(call: dict) -> str:
    s = call["status"]
    a = call.get("attempts", 0)
    return {
        "confirmed": "They confirmed the reminder was done.",
        "rescheduled": "They asked to be called later; a new call is scheduled.",
        "retrying": f"No clear answer yet after {a} attempt(s); retrying soon.",
        "needs_attention": "ATTENTION: they said something worrying on the call. Please check on them.",
        "unreachable": f"Could not reach them after {a} attempt(s).",
        "calling": "The call is in progress.",
        "scheduled": "The call is scheduled.",
    }.get(s, f"Status: {s}.")


def reporter_agent(call: dict, events: list) -> str:
    lines = [f"{e['agent']}: {e['text']}" for e in events[-25:]]
    user = (f"Reminder: {call['message']}\nStatus: {call['status']}\nAttempts: {call.get('attempts', 0)}\n"
            "Transcript:\n" + "\n".join(lines))
    try:
        out = ask_json(REPORTER_SYSTEM, user, max_tokens=80, timeout=8)
        summary = str(out.get("summary", "")).strip()
        if not summary:
            raise ValueError("empty summary")
        if call["status"] == "needs_attention" and not summary.upper().startswith("ATTENTION"):
            summary = "ATTENTION: " + summary
        words = summary.split()
        if len(words) > 25:
            summary = " ".join(words[:25]).rstrip(",;.") + "."
        return summary
    except Exception:
        return _fallback_summary(call)
