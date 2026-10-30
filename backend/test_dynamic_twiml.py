import urllib.request
import json

token = "e6c81c82-2652-4826-ad15-f4c7a722bcf7"
url = f"https://webhook.site/token/{token}"

reminder = "Please remember to take your BP tablets after breakfast and drink some water."
name = "sritej"

new_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="en-IN">Hello {name}! This is CareCall calling with your reminder: {reminder}. How are you feeling today?</Say>
</Response>"""

payload = json.dumps({
    "default_status": 200,
    "default_content_type": "text/xml",
    "default_content": new_content
}).encode("utf-8")

req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
with urllib.request.urlopen(req) as res:
    print("PUT response status:", res.status)

# Verify public endpoint
probe_url = f"https://webhook.site/{token}"
with urllib.request.urlopen(probe_url) as probe:
    content = probe.read().decode("utf-8")
    print("Live Endpoint Response:\n", content)
