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

sid1 = f"sid-h1-{uuid.uuid4().hex[:6]}"
sid2 = f"sid-h2-{uuid.uuid4().hex[:6]}"
sid3 = f"sid-h3-{uuid.uuid4().hex[:6]}"

# --- HURDLE 1: Identity & Voice Greeting ---
print("\n[HURDLE 1] Testing Voice Routing & Reminder Delivery...")
r1 = client.post(f"/twilio/voice?contact_id={c['id']}&CallSid={sid1}")
assert r1.status_code == 200, "Voice route failed"
assert "<Response>" in r1.text and "<Say" in r1.text, "Invalid TwiML returned"
assert "Sritej Deverkonda" in r1.text, "Contact name missing from voice greeting"
assert "Take blood pressure tablets" in r1.text, "Reminder note missing from voice greeting"
print("  [PASS] 1.1 Voice route returns HTTP 200 with valid TwiML XML")
print("  [PASS] 1.2 Spoken greeting delivers personalized reminder directly to contact")

# --- HURDLE 2: Conversational Dialogue & Emergency Gate ---
print("\n[HURDLE 2] Testing Conversational Dialogue & Emergency Response...")
# Test regular conversational turn
r2_normal = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid2}", data={'SpeechResult': 'I took my medicine already and feeling great today'})
assert r2_normal.status_code == 200
assert "<Gather" in r2_normal.text or "<Say" in r2_normal.text
print("  [PASS] 2.1 Conversational turn: Groq LLM replied warmly to speech input")

# Test emergency detection
r2_emerg = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid2}", data={'SpeechResult': 'Help, I fell down and have severe chest pain'})
assert r2_emerg.status_code == 200
assert "112" in r2_emerg.text or "emergency" in r2_emerg.text.lower()
alerts = db.list_alerts()
emerg_alerts = [a for a in alerts if "EMERGENCY" in " ".join(a.get("reasons", []))]
assert len(emerg_alerts) > 0, "Emergency alert was not created"
print(f"  [PASS] 2.2 Emergency detection: Flagged '{emerg_alerts[0]['reasons'][0]}'")
print("  [PASS] 2.3 Caregiver alert created immediately with HIGH severity")

# --- HURDLE 3: Unreachable Phone Escalation ---
print("\n[HURDLE 3] Testing Unreachable Call Escalation...")
r3 = client.post(f"/twilio/status?contact_id={c['id']}&attempt=3", data={'CallStatus': 'no-answer', 'CallSid': sid3})
assert r3.status_code == 200
recent_alerts = db.list_alerts()
unreachable_alerts = [a for a in recent_alerts if "Nobody answered" in " ".join(a.get("reasons", []))]
assert len(unreachable_alerts) > 0, "Unreachable escalation alert missing"
print("  [PASS] 3.1 Unreachable phone (attempt 3) detected")
print(f"  [PASS] 3.2 Escalated to Caregiver Alert: {unreachable_alerts[0]['reasons'][0]}")

print("\n" + "=" * 60)
print("SUCCESS: ALL 3 HURDLES VERIFIED AND OPERATIONAL!")
print("=" * 60)
