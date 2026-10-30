import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

serve(async (req: Request) => {
  const url = new URL(req.url);
  const contactId = url.searchParams.get("contact_id");
  const callSid = url.searchParams.get("CallSid") || "sid-" + Date.now();

  let contactName = "there";
  let reminder = "Take your medication and drink some water";
  let lang = "en-IN";

  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "https://ujgqwmhartcreiyuylhb.supabase.co";
  const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";

  if (contactId && supabaseKey) {
    try {
      const supabase = createClient(supabaseUrl, supabaseKey);
      const { data } = await supabase.from("contacts").select("*").eq("id", contactId).maybeSingle();
      if (data) {
        if (data.name) contactName = data.name;
        if (data.reminders) reminder = data.reminders;
        if (data.language) lang = data.language;
      }
    } catch (e) {
      console.error("DB error in twilio-voice:", e);
    }
  }

  const respondUrl = `${supabaseUrl}/functions/v1/twilio-respond?contact_id=${encodeURIComponent(contactId || "")}&amp;call_sid=${encodeURIComponent(callSid)}`;

  const greeting = reminder
    ? `Hello ${contactName}, this is CareCall. I am calling with your reminder: ${reminder}. How are you feeling today?`
    : `Hello ${contactName}, this is your CareCall check-in. How are you feeling today?`;

  const twiml = `<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Gather input="speech" action="${respondUrl}" method="POST" speechTimeout="auto" language="${lang}" timeout="6">
    <Say voice="alice" language="${lang}">${greeting}</Say>
  </Gather>
  <Redirect method="POST">${respondUrl}</Redirect>
</Response>`;

  return new Response(twiml, {
    status: 200,
    headers: {
      "Content-Type": "text/xml; charset=utf-8",
    },
  });
});
