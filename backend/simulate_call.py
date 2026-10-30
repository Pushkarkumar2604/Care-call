"""Pretend to be Twilio + the person, so you can test the whole call flow with DRY_RUN=true
(no phone, no Twilio minutes). The backend must be running on port 8000.

Usage:
  python simulate_call.py <call_id> "what you say" ["next thing you say" ...]
  python simulate_call.py latest "which tablet?" "call me in 5 minutes"
  python simulate_call.py latest 1                 # keypad: 1 done, 2 later, 3 help
  python simulate_call.py latest --no-answer       # phone not picked up
  python simulate_call.py latest --silence         # picks up, says nothing

'latest' = the most recent call whose status is 'calling'.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"
try:
    sys.stdout.reconfigure(encoding="utf-8")  # Hindi/Kannada text in PowerShell
except Exception:
    pass


def post(path: str, data: dict | None = None) -> str:
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def show(twiml: str):
    for said in re.findall(r"<Say[^>]*>(.*?)</Say>", twiml, re.S):
        print("   CareCall says:", said)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    call_id, replies = sys.argv[1], sys.argv[2:]
    if call_id == "latest":
        calls = json.load(urllib.request.urlopen(BASE + "/api/calls"))
        calling = [c for c in calls if c["status"] == "calling"]
        if not calling:
            sys.exit("No call is in 'calling' status. Schedule one (or press 'Call now') and wait for it to fire.")
        call_id = calling[0]["id"]
    call = next(c for c in json.load(urllib.request.urlopen(BASE + "/api/calls")) if c["id"] == call_id)
    attempt = call["attempts"]
    print(f"Simulating attempt {attempt} for call {call_id}")

    if replies == ["--no-answer"]:
        print(post(f"/twilio/status?call_id={call_id}&attempt={attempt}", {"CallStatus": "no-answer"}))
        return

    twiml = post(f"/twilio/voice?call_id={call_id}&attempt={attempt}")
    show(twiml)
    if replies == ["--silence"]:
        replies = []
        while "<Gather" in twiml:
            url = re.search(r'<Redirect[^>]*>(.*?)</Redirect>', twiml).group(1).replace("&amp;", "&")
            print("   Person: (silence)")
            twiml = post(url)
            show(twiml)
    for reply in replies:
        m = re.search(r'<Gather[^>]*action="([^"]+)"', twiml)
        if not m:
            break
        url = m.group(1).replace("&amp;", "&")
        print("   Person:", reply)
        data = {"Digits": reply} if reply.isdigit() else {"SpeechResult": reply, "Confidence": "0.9"}
        twiml = post(url, data)
        show(twiml)
    if "<Hangup" not in twiml:
        print("   (person hangs up)")
    post(f"/twilio/status?call_id={call_id}&attempt={attempt}", {"CallStatus": "completed", "CallDuration": "30"})
    print("Done. Check the dashboard.")


if __name__ == "__main__":
    main()
