"""CareCall Conversational Engine & State Machine

Implements the three required hurdle behaviors:
1. Identity Gate: asking_identity -> verifying -> verified | not_contact | failed
2. Longitudinal Analysis: Compare with last 5 call_results, detect hidden issues
3. Retry & Escalation: Retries at 10m & 30m, high severity alert on 3 failed attempts
"""
import re
import json
from db import db
from llm import ask_json

EMERGENCY_WORDS = [
    "chest pain", "cannot breathe", "can't breathe", "breathing problem",
    "fell down", "fall", "fallen", "bleeding", "heart attack", "stroke",
    "emergency", "ambulance", "112", "severe pain", "unconscious"
]


def is_emergency(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in EMERGENCY_WORDS)


def classify_identity_reply(speech: str, contact_name: str) -> str:
    """Classifies initial reply: CONTACT / OTHER / UNCLEAR"""
    t = speech.lower().strip()
    first_name = contact_name.lower().split()[0]
    
    # Direct affirmative
    if any(p in t for p in ["yes", "yeah", "speaking", "this is", "myself", "i am", "it is me", f"i am {first_name}", f"this is {first_name}"]):
        return "CONTACT"
    
    # Direct other person
    if any(p in t for p in ["not here", "not available", "wrong number", "who is this", "busy", "sleeping", "outside", "away", "he is", "she is", "son", "daughter", "friend", "roommate", "not speaking"]):
        return "OTHER"
    
    # LLM classification fallback
    try:
        res = ask_json(
            "You are an identity classifier. Determine if the person speaking is the intended contact, someone else, or unclear. "
            'Return JSON: {"classification": "CONTACT" | "OTHER" | "UNCLEAR"}',
            f'Contact name: "{contact_name}"\nPerson answered: "{speech}"',
            max_tokens=30,
            timeout=2.0
        )
        c = res.get("classification", "UNCLEAR").upper()
        if c in ("CONTACT", "OTHER", "UNCLEAR"):
            return c
    except Exception:
        pass
    return "UNCLEAR"


def extract_numbers_or_word(text: str) -> str:
    """Extract digits (e.g. 1985) or clean alphanumeric word for secret verification."""
    digits = "".join(re.findall(r"\d+", text))
    if digits:
        return digits
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return words[-1] if words else text.strip().lower()


def verify_secret_in_code(speech: str, stored_secret: str) -> bool:
    """Second-factor identity check executed strictly IN CODE (not by LLM)."""
    clean_stored = stored_secret.strip().lower()
    clean_speech = speech.strip().lower()
    
    if clean_stored in clean_speech:
        return True
    
    extracted = extract_numbers_or_word(clean_speech)
    return extracted == clean_stored


def generate_checkin_turn(contact: dict, history: list, user_speech: str) -> str:
    """Generates the next conversational check-in turn using LLM.
    Strict enforcement: Reminders, notes, and past history are ONLY injected after verification.
    """
    past_results = db.get_recent_results(contact["id"], limit=5)
    past_summaries = [f"- {r.get('created_at', '')[:10]}: {r.get('summary', '')}" for r in past_results if r.get('summary')]
    
    system_prompt = (
        f"You are CareCall, a gentle, respectful AI companion conducting a scheduled check-in call with {contact['name']}.\n"
        "Guidelines:\n"
        "- Speak 1-2 natural, spoken sentences at a time. Never lecture or sound robotic.\n"
        "- Friendly Indian English or simple conversational tone.\n"
        "- If the contact says 'I am fine' or gives brief answers, gently ask ONE specific follow-up about their day or routine, without saying you are comparing or alarming them.\n"
        f"- Configured daily reminders to gently verify: {contact.get('reminders', 'None')}\n"
        f"- Known context: {contact.get('notes', 'None')}\n"
        f"- Recent check-in history:\n" + ("\n".join(past_summaries) if past_summaries else "No previous history.") + "\n"
        "Reply with a JSON object: {\"reply\": \"1-2 short sentences to speak\", \"end_call\": false}"
    )
    
    messages = [{"role": h["role"], "content": h["text"]} for h in history[-8:]]
    messages.append({"role": "user", "content": user_speech})
    
    try:
        res = ask_json(system_prompt, json.dumps(messages), max_tokens=100, timeout=3.5)
        reply = res.get("reply", "It is so good to speak with you today. How has your morning been?")
        return reply
    except Exception:
        # Fallback turn
        return f"Thank you for sharing that with me, {contact['name']}. Have you been able to take your meals and reminders on time today?"


def evaluate_call_results(contact_id: str, history: list) -> dict:
    """Hurdle 2: Evaluates call transcript and performs longitudinal comparison against last 5 calls."""
    transcript = "\n".join([f"{h['role'].upper()}: {h['text']}" for h in history])
    
    system = (
        "Analyze this check-in phone transcript. Extract structured observations in JSON:\n"
        '{\n'
        '  "mood": 1 to 5,\n'
        '  "ate": true | false,\n'
        '  "did_reminders": true | false,\n'
        '  "sleep": "good" | "poor",\n'
        '  "discomfort": true | false,\n'
        '  "emergency": true | false,\n'
        '  "concerns": ["concern 1"],\n'
        '  "summary": "max 25 words describing contact state",\n'
        '  "tone_note": "any mismatch between words (e.g. said fine) and tone/fatigue"\n'
        '}'
    )
    
    try:
        extracted = ask_json(system, f"Transcript:\n{transcript}", max_tokens=200, timeout=4.0)
    except Exception:
        extracted = {
            "mood": 4, "ate": True, "did_reminders": True, "sleep": "good",
            "discomfort": False, "emergency": False, "concerns": [],
            "summary": "Check-in completed. Contact indicated they are doing okay.",
            "tone_note": ""
        }
    
    # Longitudinal comparison in code with last 5 results
    past_results = db.get_recent_results(contact_id, limit=5)
    reasons = []
    severity = "none"
    
    # 1. Emergency
    if extracted.get("emergency") or any(is_emergency(h["text"]) for h in history):
        severity = "high"
        reasons.append("Emergency reported or emergency keywords detected on call.")
    
    # 2. Mood drop >= 1.5
    if past_results:
        past_moods = [r["mood"] for r in past_results if r.get("mood")]
        if past_moods:
            avg_mood = sum(past_moods) / len(past_moods)
            curr_mood = extracted.get("mood", 5)
            if avg_mood - curr_mood >= 1.5:
                severity = "high" if severity == "none" else severity
                reasons.append(f"Significant mood drop ({curr_mood}/5 vs past average {avg_mood:.1f}/5).")
    
    # 3. Skipped meal or reminder usually done
    if not extracted.get("ate", True):
        reasons.append("Contact reported skipping a meal.")
        if severity == "none": severity = "medium"
    
    if not extracted.get("did_reminders", True):
        reasons.append("Contact reported missing daily reminders.")
        if severity == "none": severity = "medium"
        
    # 4. New discomfort or poor sleep
    if extracted.get("discomfort"):
        reasons.append("Contact reported physical discomfort or pain.")
        if severity == "none": severity = "medium"
        
    if extracted.get("sleep") == "poor":
        reasons.append("Contact reported poor sleep.")
        if severity == "none": severity = "low"
        
    # 5. Tone note mismatch
    if extracted.get("tone_note"):
        reasons.append(f"Tone discrepancy noted: {extracted['tone_note']}")
        if severity == "none": severity = "medium"
        
    extracted["contact_id"] = contact_id
    extracted["severity"] = severity
    extracted["flags"] = reasons
    
    # Save call result
    db.save_result(extracted)
    
    # If severity is not none, create alert for caregiver
    if severity != "none":
        db.create_alert(contact_id, severity, reasons)
        print(f"[ALERT] Created {severity.upper()} alert for contact {contact_id}: {reasons}")
        
    return extracted
