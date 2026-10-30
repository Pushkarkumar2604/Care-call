import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const EMERGENCY_WORDS = [
  "chest pain", "cannot breathe", "can't breathe", "breathing problem",
  "fell down", "fall", "fallen", "bleeding", "heart attack", "stroke",
  "emergency", "ambulance", "112", "severe pain", "unconscious"
];

function isEmergency(text: string): boolean {
  const t = text.toLowerCase();
  return EMERGENCY_WORDS.some((w) => t.includes(w));
}

serve(async (req: Request) => {
  const url = new URL(req.url);
  const contactId = url.searchParams.get("contact_id") || "";
  const callSid = url.searchParams.get("call_sid") || "";

  let speech = "";
  try {
    const formData = await req.formData();
    speech = (formData.get("SpeechResult") || formData.get("Digits") || "").toString().trim();
  } catch (_) {
    // Form parse fallback
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "https://ujgqwmhartcreiyuylhb.supabase.co";
  const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  const supabase = supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;

  let contactName = "there";
  let lang = "en-IN";

  if (supabase && contactId) {
    try {
      const { data } = await supabase.from("contacts").select("*").eq("id", contactId).maybeSingle();
      if (data) {
        if (data.name) contactName = data.name;
        if (data.language) lang = data.language;
      }
    } catch (e) {
      console.error("DB error in twilio-respond:", e);
    }
  }

  const respondUrl = `${supabaseUrl}/functions/v1/twilio-respond?contact_id=${encodeURIComponent(contactId)}&amp;call_sid=${encodeURIComponent(callSid)}`;

  let twiml = "";

  // 1. Emergency Check
  if (isEmergency(speech)) {
    if (supabase && contactId) {
      await supabase.from("alerts").insert({
        contact_id: contactId,
        severity: "high",
        reasons: ["EMERGENCY keywords detected during check-in call."],
        resolved: false,
      });
    }
    twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="${lang}">Please call emergency services at 112 right now. I am alerting your caregiver immediately.</Say>
  <Hangup/>
</Response>`;
  }

  // 2. Wrap up
  else if (["goodbye", "bye", "that's all", "nothing else", "talk later", "all done", "all set"].some((w) => speech.toLowerCase().includes(w))) {
    twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="alice" language="${lang}">Thank you for checking in with me today, ${contactName}. Please take care and have a wonderful day! Goodbye.</Say>
  <Hangup/>
</Response>`;
  }

  // 3. Conversational follow-up
  else {
    const reply = speech
      ? `Thank you for sharing that with me, ${contactName}. Have you been able to take your meals and daily reminders today?`
      : `Hello ${contactName}, I am here for your check-in. Did you take your reminders today, or how are you feeling?`;

    twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather input="speech" action="${respondUrl}" method="POST" speechTimeout="auto" language="${lang}" timeout="7">
    <Say voice="alice" language="${lang}">${reply}</Say>
  </Gather>
</Response>`;
  }

  return new Response(twiml, {
    status: 200,
    headers: {
      "Content-Type": "text/xml; charset=utf-8",
    },
  });
});
