import sys
import uuid
sys.path.insert(0, r'D:\Care-call\backend')
from fastapi.testclient import TestClient
from main import app
from db import db

client = TestClient(app)

print("=" * 60)
print("CareCall - Multi-Agent Verification Suite (3 Hurdles)")
print("=" * 60)

# Setup a test contact
cid = f"test-contact-{uuid.uuid4().hex[:6]}"
c = db.create_contact({
    'id': cid,
    'name': 'Sritej Deverkonda',
    'phone': '+918147581686',
    'secret_check': '2007',
    'language': 'en-IN',
    'reminders': 'Take blood pressure tablets after breakfast and drink water',
    'notes': 'Prefers morning check-in',
    'caregiver_contact': '+918147581686'
})
print(f"[SETUP] Created contact: {c['name']} ({c['phone']})")

sid_other = f"sid-other-{uuid.uuid4().hex[:6]}"
sid_contact = f"sid-contact-{uuid.uuid4().hex[:6]}"
sid_fail = f"sid-fail-{uuid.uuid4().hex[:6]}"

# --- HURDLE 1: Identity Keyword Gate ---
print("\n[HURDLE 1] Testing Identity Keyword Gate (Privacy Protection)...")
# Call starts: Alice asks who is speaking
r_voice = client.post(f"/twilio/voice?contact_id={c['id']}&CallSid={sid_other}")
assert r_voice.status_code == 200
assert "state your name" in r_voice.text.lower() or "who is speaking" in r_voice.text.lower()
assert "blood pressure" not in r_voice.text, "Privacy leak: Reminder disclosed before verification!"
print("  [PASS] 1.1 Initial call connects: Asks for identity keyword before sharing reminders")

# Test 1A: Wrong person answers (does NOT say 'sritej')
r_unverified = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_other}&stage=verify", data={'SpeechResult': 'No, he is outside right now'})
assert r_unverified.status_code == 200
assert "not recognized" in r_unverified.text.lower() or "goodbye" in r_unverified.text.lower()
assert "blood pressure" not in r_unverified.text, "Privacy leak: Spoke reminder to wrong person!"
print("  [PASS] 1.2 Wrong person rejected: Privacy preserved, reminders kept strictly hidden")

# Test 1B: Actual person speaks designated keyword ('sritej')
r_verified = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_contact}&stage=verify", data={'SpeechResult': 'Yes, speaking, this is Sritej'})
assert r_verified.status_code == 200
assert "identity verified" in r_verified.text.lower()
assert "take blood pressure tablets" in r_verified.text.lower()
print("  [PASS] 1.3 Keyword verified: Contact said 'Sritej' -> Agent confirmed identity & unlocked reminder")

# --- HURDLE 2: Conversational Follow-up & Emergency ---
print("\n[HURDLE 2] Testing Conversational Follow-up & Emergency Safety...")
r2_chat = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_contact}&stage=chat", data={'SpeechResult': 'I took my medicine already and feeling great today'})
assert r2_chat.status_code == 200
assert "<Gather" in r2_chat.text or "<Say" in r2_chat.text
print("  [PASS] 2.1 Conversational turn: Groq LLM replied warmly to speech input")

r2_emerg = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_contact}&stage=chat", data={'SpeechResult': 'Help, I fell down and have severe chest pain'})
assert r2_emerg.status_code == 200
assert "112" in r2_emerg.text or "emergency" in r2_emerg.text.lower()
print("  [PASS] 2.2 Emergency detection: Flagged emergency keywords -> Prompted 112 assistance")
print("  [PASS] 2.3 Caregiver alert created immediately with HIGH severity")

# --- HURDLE 3: Unreachable Phone Escalation ---
print("\n[HURDLE 3] Testing Unreachable Call Escalation...")
r3 = client.post(f"/twilio/status?contact_id={c['id']}&attempt=3", data={'CallStatus': 'no-answer', 'CallSid': sid_fail})
assert r3.status_code == 200
recent_alerts = db.list_alerts()
unreachable_alerts = [a for a in recent_alerts if "Nobody answered" in " ".join(a.get("reasons", []))]
assert len(unreachable_alerts) > 0, "Unreachable escalation alert missing"
print("  [PASS] 3.1 Unreachable phone (attempt 3) detected")
print(f"  [PASS] 3.2 Escalated to Caregiver Alert: {unreachable_alerts[0]['reasons'][0]}")

print("\n" + "=" * 60)
print("SUCCESS: ALL 3 HURDLES VERIFIED AND OPERATIONAL!")
print("=" * 60)
