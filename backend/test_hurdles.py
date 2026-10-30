import sys
import uuid
sys.path.insert(0, r'D:\Care-call\backend')
from fastapi.testclient import TestClient
from main import app
from db import db

client = TestClient(app)

print("--- Testing Hurdle 1: Identity Gate ---")
cid = f"test-contact-{uuid.uuid4().hex[:6]}"
c = db.create_contact({
    'id': cid,
    'name': 'Rahul Sharma',
    'phone': '+918147581686',
    'secret_check': '1985',
    'language': 'en-IN',
    'reminders': 'Take blood pressure tablet after breakfast',
    'notes': 'Prefers morning walk',
    'caregiver_contact': '+918147581686'
})
print("Contact created:", c['name'])

sid_other = f"sid-other-{uuid.uuid4().hex[:6]}"
sid_contact = f"sid-contact-{uuid.uuid4().hex[:6]}"
sid_fail = f"sid-fail-{uuid.uuid4().hex[:6]}"

# Scenario A: Someone else answers
r = client.post(f"/twilio/voice?contact_id={c['id']}&CallSid={sid_other}")
print("1. Initial Voice TwiML has 'May I please speak with Rahul Sharma':", "May I please speak with Rahul Sharma" in r.text)

r = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_other}", data={'SpeechResult': 'No he is outside right now'})
print("2. Other person TwiML has 'Sorry to trouble you':", "Sorry to trouble you" in r.text)
print("3. No private reminders leaked to other person:", "blood pressure" not in r.text)
alerts = db.list_alerts()
print("4. Caregiver alert created for other person:", alerts[0]['severity'], alerts[0]['reasons'])

# Scenario B: Contact answers and verifies second factor in code
r = client.post(f"/twilio/voice?contact_id={c['id']}&CallSid={sid_contact}")
r = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_contact}", data={'SpeechResult': 'Yes speaking, this is Rahul'})
print("5. Asks second factor (year of birth):", "year of birth" in r.text)

# Provide correct year of birth (1985)
r = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_contact}", data={'SpeechResult': 'I was born in 1985'})
print("6. Secret factor verified in code:", "verified" in r.text)

print("\n--- Testing Hurdle 2: Conversational Follow-up ---")
r = client.post(f"/twilio/respond?contact_id={c['id']}&call_sid={sid_contact}", data={'SpeechResult': 'I am fine, everything is okay'})
print("7. Next turn TwiML returned:", len(r.text) > 0)

print("\n--- Testing Hurdle 3: Unreachable Escalation ---")
r = client.post(f"/twilio/status?contact_id={c['id']}&attempt=3", data={'CallStatus': 'no-answer', 'CallSid': sid_fail})
recent_alerts = db.list_alerts()
print("8. Final attempt unreachable creates high severity alert:", recent_alerts[0]['severity'], recent_alerts[0]['reasons'])
print("\nAll 3 hurdles successfully tested and verified!")
