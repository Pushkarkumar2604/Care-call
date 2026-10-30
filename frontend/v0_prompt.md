# Prompt to paste into v0.dev (Satyam)

Paste everything between the lines into a new v0 chat.

---
Build a single-page Next.js (App Router) client component in app/page.tsx using only React and Tailwind (no shadcn, no other libraries, no API routes, no Supabase client). App name: "CareCall – multi-agent reminder calls, Team Carpe Diem (Ah-11)". Teal header.

It talks to an external FastAPI backend whose base URL the user types into a text box in the header (save it in localStorage key "carecall_api", wrap localStorage in try/catch). Every fetch must send the headers {"Content-Type":"application/json","ngrok-skip-browser-warning":"true"}.

Left column: a "Schedule a call" form with fields phone (placeholder +919876543210), message (textarea, required), note for questions (optional textarea), your name (optional), date and time (datetime-local, default = now + 1 minute), language select (en English, hi Hindi, kn Kannada). On submit: POST {base}/api/calls with JSON {phone, message, note, sender_name, language, scheduled_at: new Date(when).toISOString()}. Show "Planner agent is thinking…" while waiting, then "Scheduled. Priority: <priority>" or the error from response.detail.

Right column: a live list of calls. Poll GET {base}/api/calls every 2 seconds. It returns an array of {id, phone, message, language, priority, status, scheduled_at, attempts, max_attempts, summary, events:[{id, agent, text, created_at}]}. For each call show a card with: a colored status pill (scheduled indigo, calling amber, confirmed green, rescheduled sky, retrying orange, needs_attention solid red with white text and the whole card gets a thick red border, unreachable gray; show needs_attention as "needs attention"), a priority pill (critical red, high amber, normal gray), the phone, language, scheduled time, attempts, the message in gray, the summary as "Reporter: …" (bold red if needs_attention), a "Call now" button that POSTs {base}/api/calls/{id}/call-now, and a collapsible "Transcript (n lines)" that lists events with time, a small colored tag per agent (PLANNER violet, LISTENER blue, GUARDIAN yellow, REPORTER green, SCHEDULER slate, TWILIO pink, PERSON sky, CARECALL teal, SYSTEM red) and the text. Keep open/closed state of transcripts across polls.

Header right side: the backend URL input, a status text from GET {base}/api/health (show "DRY RUN" if dry_run is true else "LIVE CALLS", plus db and llm), and a "Reset demo" button that asks confirm() then sends DELETE {base}/api/calls.

Responsive: one column on phones. Clean, large readable text.
---

If v0's result does not match, say in v0: "Replace app/page.tsx with exactly this code:" and paste `frontend/page.tsx` from our repo.
Then click **Publish** (Deploy to Vercel). Open the Vercel URL, paste the ngrok URL in the header box.
