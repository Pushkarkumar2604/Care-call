import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient
from main import app
from db import db

client = TestClient(app)

print("--- Testing CareCall Check-in & Reminder Flow ---")
contacts = db.list_contacts()
if not contacts:
    contact = db.create_contact({
        "name": "Rahul",
        "phone": "+918147581686",
        "secret_check": "1985",
        "language": "en-IN",
        "reminders": "Take your blood pressure tablet after breakfast and drink water",
        "notes": "Prefers morning tea",
        "caregiver_contact": "+918147581686"
    })
else:
    contact = contacts[0]

print(f"Contact: {contact['name']} ({contact['phone']})")
print(f"Reminder: {contact.get('reminders')}")

# 1. Voice call connects
r1 = client.post(f"/twilio/voice?contact_id={contact['id']}&CallSid=test-call-101")
print(f"\n1. Initial Call TwiML (HTTP {r1.status_code}):")
print(r1.text)
assert "CareCall" in r1.text
assert "reminder" in r1.text.lower() or "feeling" in r1.text.lower()

# 2. Contact replies: "Yes, I took my tablet and I am feeling great today"
r2 = client.post(
    f"/twilio/respond?contact_id={contact['id']}&call_sid=test-call-101",
    data={"SpeechResult": "Yes, I took my tablet after breakfast and I am feeling great today"}
)
print(f"\n2. Conversational Response TwiML (HTTP {r2.status_code}):")
print(r2.text)
assert len(r2.text) > 0
assert "<Gather" in r2.text or "<Say" in r2.text

# 3. Contact wraps up: "Thank you, goodbye"
r3 = client.post(
    f"/twilio/respond?contact_id={contact['id']}&call_sid=test-call-101",
    data={"SpeechResult": "Thank you, goodbye!"}
)
print(f"\n3. Wrap-up Response TwiML (HTTP {r3.status_code}):")
print(r3.text)
assert "Goodbye" in r3.text or "goodbye" in r3.text or "<Hangup/>" in r3.text

print("\nSUCCESS: The friendly reminder and check-in call flow works end-to-end!")
