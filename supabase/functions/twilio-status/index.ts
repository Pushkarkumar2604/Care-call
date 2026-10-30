import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

serve(async (req: Request) => {
  const url = new URL(req.url);
  const contactId = url.searchParams.get("contact_id") || "";
  const attemptStr = url.searchParams.get("attempt") || "1";
  const attempt = parseInt(attemptStr, 10);

  let status = "completed";
  let callSid = "";
  try {
    const formData = await req.formData();
    status = (formData.get("CallStatus") || "completed").toString();
    callSid = (formData.get("CallSid") || "").toString();
  } catch (_) {
    // Form parse error fallback
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") || "https://ujgqwmhartcreiyuylhb.supabase.co";
  const supabaseKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
  const supabase = supabaseKey ? createClient(supabaseUrl, supabaseKey) : null;

  if (supabase && contactId) {
    try {
      // 1. Record call attempt
      await supabase.from("call_attempts").insert({
        contact_id: contactId,
        attempt_no: attempt,
        twilio_sid: callSid,
        status: status,
        started_at: new Date().toISOString(),
      });

      // 2. HURDLE 3: If no-answer, busy, or failed
      if (["no-answer", "busy", "failed", "canceled"].includes(status)) {
        const { data: contact } = await supabase.from("contacts").select("name").eq("id", contactId).maybeSingle();
        const contactName = contact?.name || "contact";

        if (attempt >= 3) {
          // Final attempt failed -> High severity alert for caregiver
          await supabase.from("alerts").insert({
            contact_id: contactId,
            severity: "high",
            reasons: [`Could not reach ${contactName} after 3 attempts.`],
            resolved: false,
          });
          console.log(`[ALERT] Escalated: 3 failed attempts for ${contactName}`);
        } else {
          console.log(`[RETRY] Attempt ${attempt} failed (${status}). Retry scheduled.`);
        }
      }
    } catch (err) {
      console.error("Status callback error:", err);
    }
  }

  return new Response(JSON.stringify({ ok: true, status }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
});
