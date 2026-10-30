# CareCall: Build Guide

**AfterHours 1.0, Final Round · Theme 01: Agentic AI and Multi-Agent Systems**
**Team Carpe Diem (Ah-11):** Sritej (lead), Pushkar Singh, Satyam Tiwari
**Platform:** Windows, PowerShell, VS Code

> **How to use this guide.** The full code is in Section 4 and in the `carecall/` folder that comes with this guide. Building means setting up the tools, then adding the code in the order of Section 3. After each step, run its check before you move on. The backend code was tested end to end in DRY_RUN mode with SQLite: it scheduled calls and handled done, later, question, needs attention, no answer, silence and unreachable, plus the restore after a restart. It has **not** been tested against your real Twilio, Groq or Supabase accounts, so do the "check" steps in order.

---

## 1. Architecture overview (plain words)

**What it does.** A family member fills in a short form with a phone number, a message, a time and a language. At that time an AI agent phones the person. It speaks the message and asks "Did you do it?", then listens to the reply, which can be speech or a keypad press. Depending on the reply it confirms, calls back later, answers a question using only the sender's note, flags a worrying reply, or retries if nobody answers. Afterwards it writes a one-line report that the sender sees on a live dashboard.

```
 ┌──────────────┐  HTTPS (poll every 2 s)  ┌──────────────────────────────────────────┐
 │ v0 React page│ ───────────────────────▶ │        FastAPI backend (laptop)          │
 │ (on Vercel)  │ ◀─────────────────────── │  /api/...   form + dashboard data        │
 └──────────────┘                          │  /twilio/...webhooks during the call     │
                                           │                                          │
   ┌────────────┐      ngrok tunnel        │  APScheduler ── fires / retries calls    │
   │  Twilio    │ ◀──────────────────────▶ │  4 agents (plain Python functions):      │
   │  Voice     │   "what do I say next?"  │   Planner · Listener · Guardian · Reporter│
   └─────┬──────┘                          │  supabase client ──▶ Supabase Postgres   │
         │ phone call                      │  (SQLite fallback)                       │
   ┌─────▼──────┐                          │  Groq / Claude API (keys stay here)      │
   │ Elder's    │                          └──────────────────────────────────────────┘
   │ phone      │
   └────────────┘
```

**The one rule to remember:** everything goes through the FastAPI backend. Twilio reaches only the backend, through ngrok. The v0 page talks only to the backend. Only the backend talks to Supabase and the LLM, so no secret key ever reaches the browser. We use **polling every 2 seconds** for live updates instead of Supabase Realtime. Polling needs no Supabase keys in the browser, has no extra setup, and keeps working on SQLite.

**The four agents.** Each is a separate LLM call with its own short prompt and a strict JSON output. Each one also has a non-AI fallback.

| Agent | When it runs | Input → Output (JSON) | If the LLM fails |
|---|---|---|---|
| **Planner** | When a reminder is scheduled | message → `{priority, reason, spoken_message}`. Priority sets the retry plan; `spoken_message` is the message translated to the call language | Keyword rule (tablet/medicine → critical, doctor/appointment → high); message spoken as typed |
| **Listener** | Live, after every reply | what they said → `{outcome: DONE/LATER/QUESTION/WORRYING/UNCLEAR, minutes, reply}` | Keyword matcher in English, Hindi and Kannada. Keypad 1/2/3 never needs the LLM |
| **Guardian** | Only before speaking an answer to a question | note + answer → `{ok, reason}` | Fails closed: says "I don't have that information, I'll let the sender know." |
| **Reporter** | After each call attempt ends | transcript → `{summary}` (max 25 words, starts with "ATTENTION:" if worrying) | Template sentence per status |

**Retry plans (set by the Planner's priority):**

| Priority | Attempts before "unreachable" | Gap between tries | With `DEMO_MODE=true` |
|---|---|---|---|
| critical (medicine, health) | 3 | 2 min | 20 s |
| high (appointments) | 3 | 5 min | 20 s |
| normal (everything else) | 2 | 10 min | 20 s |

**Statuses (colored on the dashboard):** `scheduled` → `calling` → one of `confirmed`, `rescheduled` (LATER; calls again later), `retrying` (no answer or unclear; calls again), `needs attention` (red, worrying reply), `unreachable` (all attempts used up). A reply of "later" does not use up an attempt. Only no answer, silence or an unclear reply does.

**One call, step by step:**
1. The form sends a POST to `/api/calls`. The **Planner** sets the priority and retry plan, the row is saved, and **APScheduler** queues the call.
2. At the set time the scheduler runs `fire_call`, and Twilio places the call. On a trial account the person hears the trial message and presses any key.
3. When the person answers, Twilio POSTs to `/twilio/voice`. We reply with TwiML: say the intro and the message, then `<Gather input="speech dtmf">` asks "Did you do it? … press 1, 2 or 3".
4. Twilio POSTs the speech text or the digit to `/twilio/gather`, and the **Listener** decides the outcome. For a QUESTION, the **Guardian** checks the answer before it is spoken. The person is then asked again, up to 2 questions.
5. The system acts on the outcome: confirm and hang up, reschedule, mark needs attention, or re-ask once and then retry.
6. Twilio POSTs the end of the call to `/twilio/status`. If nobody gave a final answer, the retry plan runs. The **Reporter** writes the summary, and the dashboard shows it within 2 seconds.

**Files** (who owns each one, so Git conflicts stay rare):

| File | Owner | What it does |
|---|---|---|
| `backend/config.py` | Pushkar | Reads `.env`; auto-detects the ngrok URL |
| `backend/db.py` | Pushkar | Supabase + SQLite with the same functions |
| `backend/llm.py` | Pushkar | One `ask_json()` for Groq/Claude with a hard timeout |
| `backend/scheduler.py` | Pushkar | APScheduler, retry, reschedule, restore after restart |
| `backend/agents.py` | Pushkar (Planner, Reporter), Sritej (Listener, Guardian) | The 4 agents + keyword fallback |
| `backend/phrases.py` | Sritej | Fixed spoken lines in en / hi / kn |
| `backend/telephony.py` | Sritej | Place calls, build TwiML |
| `backend/main.py` | Pushkar (API), Sritej (`/twilio/*`) | FastAPI routes |
| `backend/hello_call.py` | Sritej | Hour 0–1 "hello" call and voice test |
| `backend/simulate_call.py` | Sritej | Fakes Twilio + the person for DRY_RUN tests |
| `backend/static/index.html` | Satyam | Fallback UI served by FastAPI |
| `frontend/v0_prompt.md`, `frontend/page.tsx` | Satyam | v0 prompt + reference React page |
| `supabase/schema.sql` | Pushkar | Tables |

---

## 2. Tool setup steps

Do these at hour 0. Everyone does 2.1 and 2.2 on their own laptop.

**2.1 Python 3.11 (all three)**
- Do: install Python 3.11 (not 3.12+, the brief says 3.11).
- Command (PowerShell):
  ```powershell
  winget install -e --id Python.Python.3.11
  ```
- Check: open a new PowerShell and run `py -3.11 --version`. It should print `Python 3.11.x`.

**2.2 Git and VS Code (all three)**
- Commands:
  ```powershell
  winget install -e --id Git.Git
  winget install -e --id Microsoft.VisualStudioCode
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
  ```
- In VS Code install the **Python** extension (Microsoft).
- Check: `git --version` and `code --version` both print versions.

**2.3 GitHub repository (Sritej)**
- Do: on github.com create a private repo `carecall` without a README. Under Settings → Collaborators, add Pushkar and Satyam.
- Check: both teammates accept the invite and can open the repo page.

**2.4 Twilio (Sritej)**
1. Sign up at twilio.com/try-twilio and verify your email and phone.
2. Console → **Phone Numbers → Manage → Buy a number** and take the free trial number (a US number is fine for calling India). Copy it in `+1…` form.
3. Console → **Phone Numbers → Manage → Verified Caller IDs → Add a new Caller ID**: verify **all three teammates' phones** (OTP by call or SMS). Trial accounts can only call verified numbers.
4. Console → **Voice → Settings → Geo permissions** (the page title is "Voice Geographic Permissions"): tick **India** and save.
5. Console home → **Account Info**: copy the **Account SID** and **Auth Token**.
- Check: the trial number appears under Active Numbers, the 3 numbers appear under Verified Caller IDs, and India is ticked.

**2.5 ngrok (Sritej, only on the laptop that runs the backend)**
- Do: sign up at ngrok.com and copy your authtoken from the dashboard.
- Commands:
  ```powershell
  winget install ngrok -s msstore
  ngrok config add-authtoken YOUR_TOKEN
  ngrok http 8000
  ```
  (If winget fails, download the Windows zip from ngrok.com/download, unzip it and run `.\ngrok.exe` from that folder.)
- Check: ngrok shows `Forwarding https://xxxx.ngrok-free.app -> http://localhost:8000`. Copy the https address; it is your `PUBLIC_URL`.
- Tip: the backend also asks ngrok's local API (`127.0.0.1:4040`) for the current address, so calls keep working even if you forget to update `.env`. Still update `PUBLIC_URL` each time, as a backup. Your ngrok dashboard may also show a free fixed **dev domain** under Domains. If it does, `ngrok http 8000 --url=your-domain.ngrok-free.app` keeps the same address every time.

**2.6 Groq API key (Pushkar)**
- Do: console.groq.com → API Keys → Create. Copy the `gsk_…` key. (If you have a Claude API key instead, set `LLM_PROVIDER=claude` and `ANTHROPIC_API_KEY`.)
- Check: done in step 8 with a one-line Python test.

**2.7 Supabase (Pushkar)**
1. supabase.com → New project, named `carecall`. Pick the region closest to you (Mumbai if offered) and save the database password.
2. **SQL Editor → New query**: paste `supabase/schema.sql` (Section 4) and click **Run**.
3. **Project Settings → API (API Keys)**: copy the **Project URL** and the **service_role / secret** key. Never use this key in the browser.
- Check: **Table Editor** shows the `calls` and `events` tables.

**2.8 v0 (Satyam)**
- Do: go to v0.dev, sign in with a Vercel account (free), and make sure you can create a chat and press Publish/Deploy.
- Check: a test "hello world" page deploys to a `*.vercel.app` URL.

**2.9 Windows Game Bar (Satyam)**
- Do: press **Win+G**, then Settings → Capture and turn on "Capture audio". Do a test recording with **Win+Alt+R** (start/stop).
- Check: the video appears in `Videos\Captures`.

---

## 3. Build steps (numbered, with owner and time)

Format: **Do / Tool / Commands / Check**. The times match your 24-hour plan.

### Hours 0–1: setup and first "hello" call

**Step 1. Create the repo skeleton** (Sritej, 0:00–0:15, Git + VS Code)
- Do: create the folders, add `.gitignore` (Section 4) **before anything else**, then push.
- Commands:
  ```powershell
  cd $HOME\Desktop
  git clone https://github.com/<you>/carecall.git
  cd carecall
  mkdir backend, backend\static, frontend, supabase
  code .
  # create .gitignore from Section 4, then:
  git add .
  git commit -m "skeleton"
  git push
  ```
- Check: `.gitignore` is on GitHub. Pushkar and Satyam run `git clone` and see the folders.

**Step 2. Python environment** (Pushkar, 0:15–0:30; also Sritej on the backend laptop)
- Do: add `backend/requirements.txt` (Section 4), create a virtual environment and install.
- Commands:
  ```powershell
  cd backend
  py -3.11 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   (then run Activate again)
  pip install -r requirements.txt
  ```
- Check: `python -c "import fastapi, twilio, supabase, apscheduler, groq; print('ok')"` prints `ok`. The prompt starts with `(.venv)`.

**Step 3. The .env file** (Sritej, 0:30–0:35)
- Do: copy `.env.example` to `.env` and fill in the Twilio SID, token and number. Keep `DRY_RUN=true` for now. Share the keys over WhatsApp DM, never in Git.
- Commands: `Copy-Item .env.example .env` then `code .env`
- Check: `git status` does **not** list `.env`.

**Step 4. First "hello" call** (Sritej, 0:35–0:50, Twilio)
- Do: add `config.py`, `phrases.py` and `hello_call.py`. This test calls with inline TwiML, so it needs no ngrok or backend.
- Command: `python hello_call.py +91XXXXXXXXXX`
- Check: the phone rings, you hear the trial message, press any key, and you hear "Hello from CareCall…". If it fails, see Section 6 (errors 21219 / 21215).

**Step 5. ngrok running** (Sritej, 0:50–1:00)
- Do: in a **second** PowerShell window, run `ngrok http 8000`. Paste the https address into `PUBLIC_URL` in `.env`.
- Check: http://127.0.0.1:4040 opens the ngrok inspector.

### Hours 1–3: backend, database, schedule form, scheduler

**Step 6. Config, LLM and database code** (Pushkar, 1:00–1:40)
- Do: add `config.py` (if not already added), `llm.py` and `db.py`. Set `GROQ_API_KEY`, `DB_BACKEND=supabase`, `SUPABASE_URL` and `SUPABASE_KEY` in `.env`.
- Commands:
  ```powershell
  python -c "from llm import ask_json; print(ask_json('Return a JSON object with key ok set to true.', 'test', timeout=10))"
  python -c "from db import db, BACKEND_NAME; print(BACKEND_NAME, db.list_calls())"
  ```
- Check: the first command prints `{'ok': True}`. The second prints `supabase []`. If it prints `sqlite`, Supabase failed and the reason is printed above that line (Section 6).

**Step 7. Agents, scheduler and API** (Pushkar, 1:40–2:30)
- Do: add `agents.py`, `scheduler.py`, `telephony.py`, `main.py` and `static/index.html`. Keep `DRY_RUN=true` and `DEMO_MODE=true`.
- Command: `uvicorn main:app --port 8000`
- Check: the terminal prints `[carecall] DRY_RUN=True … DB=supabase`. Opening http://localhost:8000/api/health shows JSON, and http://localhost:8000 shows the fallback page.

**Step 8. Scheduled call fires** (Pushkar, 2:30–3:00), **first milestone of the plan**
- Do: on http://localhost:8000, schedule "Take your BP tablet" for a verified number, 1 minute ahead.
- Check: the card shows **CRITICAL**, and the transcript shows a PLANNER line ending `[llm]`. Within about 60 s the uvicorn terminal prints `[DRY_RUN] Would call +91…` and the status becomes **calling**. Then stop uvicorn (Ctrl+C) and start it again with a call scheduled 5 minutes ahead. The terminal should print `[scheduler] restored call …`.

**Step 9. v0 dashboard** (Satyam, 1:00–3:00, v0 + Vercel; in parallel with steps 6–8)
- Do: paste the prompt from `frontend/v0_prompt.md` into v0. Compare the result with `frontend/page.tsx`; if it differs, tell v0 to use that code exactly. Click **Publish** to deploy to Vercel. Open the Vercel URL and paste the ngrok https URL into the "Backend URL" box.
- Check: the header shows `DRY RUN · db supabase · llm groq`. A reminder scheduled from the Vercel page appears within 2 s, also on http://localhost:8000. If the browser console (F12) shows a CORS error, see Section 6.
- Commit: `git add frontend; git commit -m "v0 page"; git push`

### Hours 3–6: speaking, listening, keypad, Listener + fallback

**Step 10. Test the call flow without a phone** (Sritej, 3:00–3:45)
- Do: with `DRY_RUN=true`, schedule a reminder, wait for `calling`, then in a third PowerShell window (venv active) run:
  ```powershell
  python simulate_call.py latest "yes I took it"
  python simulate_call.py latest "call me in 5 minutes"
  python simulate_call.py latest 3
  python simulate_call.py latest --silence
  python simulate_call.py latest --no-answer
  ```
  (Schedule a fresh reminder, or click **Call now**, before each line.)
- Check: the statuses become confirmed / rescheduled / needs attention / retrying, and each transcript shows PERSON → LISTENER `[llm]` → CARECALL lines.

**Step 11. Keyword fallback test** (Sritej, 3:45–4:00)
- Do: set `GROQ_API_KEY=wrong` in `.env`, restart uvicorn and repeat step 10. Then put the correct key back.
- Check: LISTENER lines say `[keyword fallback (…)]` and the outcomes are still correct.

**Step 12. First real two-way call** (Sritej + Pushkar's phone, 4:00–5:00, Twilio + ngrok)
- Do: set `DRY_RUN=false`, check that ngrok is running and `PUBLIC_URL` is current, and restart uvicorn. Schedule for Pushkar's phone 1 minute ahead. Pushkar answers, presses a key for the trial message, listens, then says "call me in 2 minutes". When it calls back, he says "yes, done".
- Check: the dashboard shows rescheduled, then confirmed, with Reporter summaries. The ngrok inspector (127.0.0.1:4040) shows `POST /twilio/voice 200`, `/twilio/gather 200` and `/twilio/status 200`.
- **Milestone 1** ✅: the person answers and the agent understands "later" and "done".

**Step 13. Keypad backup** (Sritej, 5:00–5:30)
- Do: one real call where you press **1**, one with **2**, one with **3**.
- Check: confirmed / rescheduled (10 min) / needs attention (red). Use about 3 Twilio minutes.

**Step 14. Hour-6 decision point** (all, 5:30–6:00)
- Supabase still failing? Set `DB_BACKEND=sqlite`. Nothing else changes.
- v0 page not working? Use http://localhost:8000 (the fallback page) for the demo.
- Commit and push everything that works.

### Hours 6–9: reschedule, retry, unreachable, needs attention, Planner

**Step 15. Retry and unreachable** (Pushkar, 6:00–7:30)
- Do: schedule "Water the plants" (normal priority, 2 attempts) for Satyam's phone and **decline** both calls.
- Check: the transcript shows `Busy / declined. Retry 2 of 2 in 20 s`, then `marked UNREACHABLE`, and the Reporter says it could not reach them.

**Step 16. Planner priorities** (Pushkar, 7:30–8:15)
- Do: schedule 3 reminders in DRY_RUN: medicine, doctor appointment, "call aunt for her birthday".
- Check: they get critical / high / normal, with 3 / 3 / 2 attempts.

**Step 17. Needs attention by speech** (Sritej, 8:15–9:00)
- Do: on a real call, say "I fell down and my knee hurts".
- Check: the call says "I am sorry to hear that…", the card turns **red**, and the Reporter line starts with `ATTENTION:`. Nobody else is contacted.

### Hours 9–11: Guardian and Reporter

**Step 18. Guardian** (Sritej, 9:00–10:00)
- Do: schedule with note "Blue tablet, box on the kitchen shelf. Take it with water." On the call, ask "Which tablet?" (answer is in the note) and then "Can I take it with tea?" (answer is not in the note).
- Check: the first gets a GUARDIAN `PASSED` and a real answer. The second gets "I don't have that information, I'll let the sender know". The GUARDIAN line shows either `BLOCKED` (the Listener tried to answer and the Guardian stopped it) or `PASSED: safe 'no information' reply` (the Listener already refused). Both are correct behaviour.

**Step 19. Reporter display** (Satyam, 9:00–11:00)
- Do: check that every finished call shows "Reporter: …" and needs attention shows in bold red.
- **Milestone 2** ✅: all four agents work end to end on one call (Planner tag → Listener → Guardian → Reporter visible in one transcript).

### Hours 11–13: dashboard polish (Satyam)

**Step 20.** Agent tags colored, status pills colored, transcript open state kept across polls, and readable on the projector (zoom the browser to 125%). Test on a phone browser too.
- Check: a judge can tell what happened from 3 metres away.

### Hours 13–15: Hindi and Kannada (Sritej)

**Step 21. Voice test** (about 2 Twilio minutes):
```powershell
python hello_call.py +91XXXXXXXXXX hi
python hello_call.py +91XXXXXXXXXX kn
```
- If a voice is silent or errors in the Twilio Debugger (Console → Monitor → Logs → Errors), try `VOICE_HI=Polly.Aditi` or another voice from Twilio's text-to-speech voice list for that language.

**Step 22. Listening test:** one full Hindi call (say "हाँ, हो गया" and "दस मिनट बाद") and one Kannada call (say "ಹೌದು" and "ಆಮೇಲೆ").
- Check: correct outcomes. **Keep a language only if both speaking and listening work.** Otherwise remove it from `ENABLED_LANGUAGES` and that call falls back to English automatically. Ask a native speaker to read the lines in `phrases.py`.

### Hours 15–20: sleep in shifts

| Time | Asleep | Awake and working on |
|---|---|---|
| 15:00–19:30 | Sritej | Pushkar: slides + pitch notes · Satyam: draft backup video (DRY_RUN with simulate_call.py is fine for screen shots) |
| 15:30–20:00 | Satyam | Pushkar (until 16:00) |
| 16:00–20:30 | Pushkar | Sritej (from 19:30): rehearse the pitch alone |

Adjust so that each person gets at least 4–5 hours and someone is always awake to watch the laptops.

### Hour 20–21: bug bash on two phones (all)

**Step 23.** Run the whole test checklist (Section 5) with `DRY_RUN=false` on Pushkar's and Satyam's phones. Write every bug on paper and fix only the ones that break the demo.

### Hour 21: feature freeze

**Step 24.** `git add . ; git commit -m "freeze" ; git tag v1-freeze ; git push --tags`. From now on, change only `.env` values or a demo-breaking bug.

### Hours 21–23: rehearse and record

**Step 25.** Rehearse the Section 7 script 3 times with a timer, clicking **Reset demo** between runs. About 3 Twilio minutes per run.
**Step 26.** Satyam records the best run with Win+G (Win+Alt+R), capturing the screen and the speakerphone audio. Save it in 2 places (laptop + Google Drive / phone).

### Hours 23–24: final checks

**Step 27.** Charge both phones, turn on the hotspot, set `DRY_RUN=false` and `DEMO_MODE=true`, start ngrok and update `PUBLIC_URL`, start uvicorn **without** `--reload`, paste the new URL into the v0 page, click **Reset demo**, then do one test call. Check Twilio balance and minutes (Console → Monitor → Usage).

**Twilio minutes budget:** about 30 for building (steps 4–22), about 20 for rehearsals, about 25 kept in reserve. Use `DRY_RUN=true` + `simulate_call.py` for every logic test.

---

## 4. Code, file by file

Folder layout:
```
carecall/
├── .gitignore
├── backend/
│   ├── .env.example        (copy to .env)
│   ├── requirements.txt
│   ├── config.py
│   ├── db.py
│   ├── llm.py
│   ├── phrases.py
│   ├── agents.py
│   ├── telephony.py
│   ├── scheduler.py
│   ├── main.py
│   ├── hello_call.py
│   ├── simulate_call.py
│   └── static/index.html   (fallback UI)
├── frontend/
│   ├── v0_prompt.md
│   └── page.tsx
└── supabase/
    └── schema.sql
```

Run commands are always from `backend\` with the venv active: `uvicorn main:app --port 8000`.

### 4.1 `.gitignore`

Repo root. Keeps keys and local files out of Git.

```text
.env
.venv/
venv/
__pycache__/
*.pyc
*.db
.vscode/
node_modules/
```

### 4.2 `backend/requirements.txt`

Exact versions (tested together).

```text
fastapi==0.162.0
uvicorn[standard]==0.54.0
python-multipart==0.0.32
twilio==9.12.0
supabase==2.33.0
APScheduler==3.11.0
groq==1.7.0
anthropic==2.3.0
python-dotenv==1.2.4
```

### 4.3 `backend/.env.example`

Copy to `.env` and fill in. Never commit `.env`.

```bash
# Copy this file to .env (same folder) and fill it in. NEVER commit .env.

# ---- Safety switches ----
# true  = print instead of placing real calls (no Twilio minutes used)
# false = real phone calls
DRY_RUN=true
# true = short retry gaps (20 s) so the "unreachable" demo finishes quickly
DEMO_MODE=true

# ---- Twilio (console.twilio.com > Account Info) ----
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
# Your Twilio trial phone number in E.164 format, e.g. +12025550123
TWILIO_FROM_NUMBER=+10000000000

# ---- ngrok ----
# The https address ngrok prints, no trailing slash. Update after every ngrok restart.
# (If ngrok is running on this laptop, the backend also auto-detects it.)
PUBLIC_URL=https://xxxx-xx-xx.ngrok-free.app

# ---- LLM: groq or claude ----
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_xxxxxxxx
GROQ_MODEL=llama-3.3-70b-versatile
ANTHROPIC_API_KEY=
CLAUDE_MODEL=claude-haiku-4-5
# seconds to wait for the LLM during a live call before using the keyword fallback
LLM_TIMEOUT=2.5

# ---- Database: supabase or sqlite ----
DB_BACKEND=supabase
SUPABASE_URL=https://xxxxxxxx.supabase.co
# Use the service_role (secret) key. It stays on the backend only.
SUPABASE_KEY=eyJhbGciOi...
SQLITE_PATH=carecall.db

# ---- Languages ----
# Remove hi or kn here if they fail testing; those calls then fall back to English.
ENABLED_LANGUAGES=en,hi,kn
# Twilio voices (change if a voice fails in testing)
VOICE_EN=Google.en-IN-Standard-A
VOICE_HI=Google.hi-IN-Standard-A
VOICE_KN=Google.kn-IN-Standard-A

# ---- CORS: * or a comma-separated list such as https://carecall.vercel.app ----
CORS_ORIGINS=*
```

### 4.4 `supabase/schema.sql`

Run once in the Supabase SQL Editor.

```sql
-- CareCall tables. Paste into Supabase > SQL Editor > New query > Run.

create table if not exists calls (
  id                uuid primary key,
  phone             text not null,
  message           text not null,
  note              text default '',
  sender_name       text default '',
  language          text not null default 'en',
  spoken_message    text default '',
  priority          text default 'normal',
  priority_reason   text default '',
  max_attempts      int  default 2,
  retry_gap_seconds int  default 600,
  attempts          int  default 0,
  failed_attempts   int  default 0,
  status            text not null default 'scheduled',
  scheduled_at      timestamptz not null,
  next_attempt_at   timestamptz,
  summary           text default '',
  twilio_sid        text default '',
  created_at        timestamptz default now(),
  updated_at        timestamptz default now()
);

create table if not exists events (
  id         bigserial primary key,
  call_id    uuid references calls(id) on delete cascade,
  agent      text not null,
  text       text not null,
  created_at timestamptz default now()
);

create index if not exists events_call_id_idx on events(call_id);

-- Lock the tables: only the backend's service_role (secret) key can read/write.
alter table calls  enable row level security;
alter table events enable row level security;
```

### 4.5 `backend/config.py`

Settings from `.env` + ngrok URL auto-detect.

```python
"""All settings come from the .env file (loaded with python-dotenv)."""
import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


DRY_RUN = _bool("DRY_RUN", "true")
DEMO_MODE = _bool("DEMO_MODE", "false")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "2.5"))

DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").strip().lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SQLITE_PATH = os.getenv("SQLITE_PATH", "carecall.db")

ENABLED_LANGUAGES = [x.strip() for x in os.getenv("ENABLED_LANGUAGES", "en,hi,kn").split(",") if x.strip()]
VOICES = {
    "en": os.getenv("VOICE_EN", "Google.en-IN-Standard-A"),
    "hi": os.getenv("VOICE_HI", "Google.hi-IN-Standard-A"),
    "kn": os.getenv("VOICE_KN", "Google.kn-IN-Standard-A"),
}

CORS_ORIGINS = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]


def get_public_url() -> str:
    """Public https address Twilio uses to reach us.

    1) If ngrok runs on this laptop, ask its local API (always the current address).
    2) Otherwise use PUBLIC_URL from .env.
    """
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=0.5) as r:
            data = json.load(r)
        for t in data.get("tunnels", []):
            if t.get("public_url", "").startswith("https://"):
                return t["public_url"].rstrip("/")
    except Exception:
        pass
    return PUBLIC_URL
```

### 4.6 `backend/db.py`

Supabase (main) and SQLite (fallback) with the same functions.

```python
"""Database layer. Same functions for Supabase (main) and SQLite (fallback).

Switch with DB_BACKEND=supabase or DB_BACKEND=sqlite in .env.
Tables:
  calls  - one row per reminder (status, priority, retry plan, summary ...)
  events - the transcript: one row per line, tagged with the agent that acted
"""
import sqlite3
import threading
from datetime import datetime, timezone

import config

PENDING = ("scheduled", "rescheduled", "retrying")

CALL_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "phone": "TEXT NOT NULL",
    "message": "TEXT NOT NULL",
    "note": "TEXT DEFAULT ''",
    "sender_name": "TEXT DEFAULT ''",
    "language": "TEXT DEFAULT 'en'",
    "spoken_message": "TEXT DEFAULT ''",
    "priority": "TEXT DEFAULT 'normal'",
    "priority_reason": "TEXT DEFAULT ''",
    "max_attempts": "INTEGER DEFAULT 2",
    "retry_gap_seconds": "INTEGER DEFAULT 600",
    "attempts": "INTEGER DEFAULT 0",
    "failed_attempts": "INTEGER DEFAULT 0",
    "status": "TEXT DEFAULT 'scheduled'",
    "scheduled_at": "TEXT",
    "next_attempt_at": "TEXT",
    "summary": "TEXT DEFAULT ''",
    "twilio_sid": "TEXT DEFAULT ''",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Supabase
# --------------------------------------------------------------------------
class SupabaseDB:
    def __init__(self):
        from supabase import create_client

        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY missing in .env")
        self.sb = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        self.sb.table("calls").select("id").limit(1).execute()  # fail fast if tables are missing

    def insert_call(self, row: dict) -> dict:
        return self.sb.table("calls").insert(row).execute().data[0]

    def get_call(self, call_id: str):
        data = self.sb.table("calls").select("*").eq("id", call_id).limit(1).execute().data
        return data[0] if data else None

    def update_call(self, call_id: str, **fields):
        fields["updated_at"] = now_iso()
        self.sb.table("calls").update(fields).eq("id", call_id).execute()

    def list_calls(self):
        return self.sb.table("calls").select("*").order("created_at", desc=True).limit(50).execute().data

    def list_pending(self):
        return self.sb.table("calls").select("*").in_("status", list(PENDING)).execute().data

    def add_event(self, call_id: str, agent: str, text: str):
        self.sb.table("events").insert(
            {"call_id": call_id, "agent": agent, "text": text, "created_at": now_iso()}
        ).execute()

    def list_events(self, call_ids):
        if not call_ids:
            return []
        return self.sb.table("events").select("*").in_("call_id", call_ids).order("id").execute().data

    def reset(self):
        self.sb.table("events").delete().gte("id", 0).execute()
        self.sb.table("calls").delete().gte("created_at", "1970-01-01").execute()


# --------------------------------------------------------------------------
# SQLite (fallback)
# --------------------------------------------------------------------------
class SqliteDB:
    def __init__(self):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(config.SQLITE_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        cols = ", ".join(f"{k} {v}" for k, v in CALL_COLUMNS.items())
        with self.lock:
            self.conn.execute(f"CREATE TABLE IF NOT EXISTS calls ({cols})")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "call_id TEXT, agent TEXT, text TEXT, created_at TEXT)"
            )
            self.conn.commit()

    def _q(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return [dict(r) for r in cur.fetchall()]

    def insert_call(self, row: dict) -> dict:
        keys = list(row.keys())
        self._q(f"INSERT INTO calls ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys])
        return self.get_call(row["id"])

    def get_call(self, call_id: str):
        rows = self._q("SELECT * FROM calls WHERE id=?", (call_id,))
        return rows[0] if rows else None

    def update_call(self, call_id: str, **fields):
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE calls SET {sets} WHERE id=?", [*fields.values(), call_id])

    def list_calls(self):
        return self._q("SELECT * FROM calls ORDER BY created_at DESC LIMIT 50")

    def list_pending(self):
        return self._q(f"SELECT * FROM calls WHERE status IN ({','.join('?' * len(PENDING))})", PENDING)

    def add_event(self, call_id: str, agent: str, text: str):
        self._q("INSERT INTO events (call_id, agent, text, created_at) VALUES (?,?,?,?)", (call_id, agent, text, now_iso()))

    def list_events(self, call_ids):
        if not call_ids:
            return []
        return self._q(f"SELECT * FROM events WHERE call_id IN ({','.join('?' * len(call_ids))}) ORDER BY id", call_ids)

    def reset(self):
        self._q("DELETE FROM events")
        self._q("DELETE FROM calls")


def _make_db():
    if config.DB_BACKEND == "supabase":
        try:
            d = SupabaseDB()
            print("[db] Using Supabase")
            return d
        except Exception as e:
            print(f"[db] Supabase not working ({e}). Falling back to SQLite.")
    print(f"[db] Using SQLite file {config.SQLITE_PATH}")
    return SqliteDB()


db = _make_db()
BACKEND_NAME = "supabase" if isinstance(db, SupabaseDB) else "sqlite"
```

### 4.7 `backend/llm.py`

One JSON-only LLM call with a hard timeout, used by all agents.

```python
"""One tiny wrapper around the LLM. Every agent calls ask_json().

- Works with Groq (LLM_PROVIDER=groq) or Claude (LLM_PROVIDER=claude).
- Always returns a dict parsed from strict JSON, or raises an exception.
- Has a hard time limit so a slow LLM never freezes the phone call.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

import config

_pool = ThreadPoolExecutor(max_workers=8)
_client = None


def _get_client():
    global _client
    if _client is None:
        if config.LLM_PROVIDER == "claude":
            import anthropic

            _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=10, max_retries=0)
        else:
            from groq import Groq

            _client = Groq(api_key=config.GROQ_API_KEY, timeout=10, max_retries=0)
    return _client


def _parse(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"LLM did not return JSON: {text[:120]}")
        return json.loads(m.group(0))


def _call(system: str, user: str, max_tokens: int) -> dict:
    client = _get_client()
    if config.LLM_PROVIDER == "claude":
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            temperature=0,
            system=system + " Reply with one JSON object only, no other text.",
            messages=[{"role": "user", "content": user}],
        )
        return _parse(resp.content[0].text)
    resp = client.chat.completions.create(
        model=config.GROQ_MODEL,
        temperature=0,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + " Reply with one JSON object only."},
            {"role": "user", "content": user},
        ],
    )
    return _parse(resp.choices[0].message.content)


def ask_json(system: str, user: str, max_tokens: int = 150, timeout: float | None = None) -> dict:
    """Run the LLM with a hard deadline. Raises on timeout or error (callers fall back)."""
    if config.LLM_PROVIDER == "claude" and not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    if config.LLM_PROVIDER != "claude" and not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    future = _pool.submit(_call, system, user, max_tokens)
    return future.result(timeout=timeout or config.LLM_TIMEOUT)
```

### 4.8 `backend/phrases.py`

Fixed spoken lines per language. Have a native speaker check hi/kn.

```python
"""Fixed sentences the call speaks, per language, plus Twilio language codes.

Ask a native speaker to check the Hindi and Kannada lines before the demo.
"""
import config

LANGS = {
    "en": {"name": "English", "code": "en-IN"},
    "hi": {"name": "Hindi", "code": "hi-IN"},
    "kn": {"name": "Kannada", "code": "kn-IN"},
}

PHRASES = {
    "en": {
        "family": "your family",
        "intro": "Hello! This is CareCall, with a reminder from {sender}.",
        "ask": "Did you do it? Say yes, or say later, or ask me a question. You can also press 1 for done, 2 for later, or 3 if you need help.",
        "reask": "Did you do it? Press 1 for done, 2 for later, or 3 for help.",
        "unclear": "Sorry, I did not catch that. Please press 1 for done, 2 for later, or 3 for help.",
        "done": "Thank you. I will let the sender know. Goodbye!",
        "later": "Okay, I will call you again in {m} minutes. Goodbye!",
        "worrying": "I am sorry to hear that. I will inform the sender right away. Please take care.",
        "noinfo": "I don't have that information, I'll let the sender know.",
        "bye_unclear": "I will try again a little later. Goodbye.",
    },
    "hi": {
        "family": "आपके परिवार",
        "intro": "नमस्ते! यह केयरकॉल है, {sender} की ओर से एक रिमाइंडर।",
        "ask": "क्या आपने यह कर लिया? हाँ बोलिए, बाद में बोलिए, या कोई सवाल पूछिए। हो गया तो 1, बाद में के लिए 2, और मदद चाहिए तो 3 दबाइए।",
        "reask": "क्या आपने यह कर लिया? हो गया तो 1, बाद में के लिए 2, मदद के लिए 3 दबाइए।",
        "unclear": "माफ़ कीजिए, मुझे समझ नहीं आया। हो गया तो 1, बाद में के लिए 2, मदद के लिए 3 दबाइए।",
        "done": "धन्यवाद। भेजने वाले को बता दिया जाएगा। नमस्ते!",
        "later": "ठीक है, {m} मिनट बाद फिर से कॉल आएगा। नमस्ते!",
        "worrying": "यह सुनकर दुख हुआ। भेजने वाले को तुरंत बताया जाएगा। अपना ध्यान रखिए।",
        "noinfo": "यह जानकारी मेरे पास नहीं है, भेजने वाले को बता दिया जाएगा।",
        "bye_unclear": "थोड़ी देर बाद फिर से कॉल आएगा। नमस्ते।",
    },
    "kn": {
        "family": "ನಿಮ್ಮ ಕುಟುಂಬದ",
        "intro": "ನಮಸ್ಕಾರ! ಇದು ಕೇರ್‌ಕಾಲ್, {sender} ಕಡೆಯಿಂದ ಒಂದು ಜ್ಞಾಪನೆ.",
        "ask": "ನೀವು ಇದನ್ನು ಮಾಡಿದಿರಾ? ಹೌದು ಎಂದು ಹೇಳಿ, ಆಮೇಲೆ ಎಂದು ಹೇಳಿ, ಅಥವಾ ಪ್ರಶ್ನೆ ಕೇಳಿ. ಮಾಡಿದ್ದರೆ 1, ಆಮೇಲೆ ಬೇಕಿದ್ದರೆ 2, ಸಹಾಯ ಬೇಕಿದ್ದರೆ 3 ಒತ್ತಿ.",
        "reask": "ನೀವು ಇದನ್ನು ಮಾಡಿದಿರಾ? ಮಾಡಿದ್ದರೆ 1, ಆಮೇಲೆ 2, ಸಹಾಯಕ್ಕೆ 3 ಒತ್ತಿ.",
        "unclear": "ಕ್ಷಮಿಸಿ, ನನಗೆ ಅರ್ಥವಾಗಲಿಲ್ಲ. ಮಾಡಿದ್ದರೆ 1, ಆಮೇಲೆ 2, ಸಹಾಯಕ್ಕೆ 3 ಒತ್ತಿ.",
        "done": "ಧನ್ಯವಾದಗಳು. ಕಳುಹಿಸಿದವರಿಗೆ ತಿಳಿಸುತ್ತೇನೆ. ನಮಸ್ಕಾರ!",
        "later": "ಸರಿ, {m} ನಿಮಿಷಗಳ ನಂತರ ಮತ್ತೆ ಕರೆ ಮಾಡುತ್ತೇನೆ. ನಮಸ್ಕಾರ!",
        "worrying": "ಕೇಳಿ ಬೇಸರವಾಯಿತು. ಕಳುಹಿಸಿದವರಿಗೆ ತಕ್ಷಣ ತಿಳಿಸುತ್ತೇನೆ. ಜಾಗ್ರತೆ ವಹಿಸಿ.",
        "noinfo": "ನನ್ನ ಬಳಿ ಆ ಮಾಹಿತಿ ಇಲ್ಲ, ಕಳುಹಿಸಿದವರಿಗೆ ತಿಳಿಸುತ್ತೇನೆ.",
        "bye_unclear": "ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಕರೆ ಮಾಡುತ್ತೇನೆ. ನಮಸ್ಕಾರ.",
    },
}


def lang_of(call: dict) -> str:
    """Language actually used for this call (falls back to English if disabled)."""
    lang = (call.get("language") or "en").lower()
    if lang not in LANGS or lang not in config.ENABLED_LANGUAGES:
        return "en"
    return lang


def p(lang: str, key: str, **kw) -> str:
    return PHRASES.get(lang, PHRASES["en"])[key].format(**kw)
```

### 4.9 `backend/agents.py`

The four agents + keyword fallback + retry plans.

```python
"""The four AI agents. Each is a plain Python function with its own short prompt.

Planner  -> runs when a reminder is scheduled: priority + retry plan (+ translation)
Listener -> runs live on the call: DONE / LATER / QUESTION / WORRYING / UNCLEAR
Guardian -> checks an answer to a question before it is spoken
Reporter -> one sentence (max 25 words) for the sender after the call

Every LLM call is wrapped in try/except with a non-AI fallback.
"""
import re

import config
from llm import ask_json
from phrases import LANGS, p

# --------------------------------------------------------------------------
# Retry plans chosen by priority
# --------------------------------------------------------------------------
PLANS = {
    "critical": {"max_attempts": 3, "retry_gap_seconds": 120},
    "high": {"max_attempts": 3, "retry_gap_seconds": 300},
    "normal": {"max_attempts": 2, "retry_gap_seconds": 600},
}
DEMO_GAP_SECONDS = 20


def plan_for(priority: str) -> dict:
    plan = dict(PLANS.get(priority, PLANS["normal"]))
    if config.DEMO_MODE:
        plan["retry_gap_seconds"] = DEMO_GAP_SECONDS
    return plan


# --------------------------------------------------------------------------
# Keyword lists (used when the LLM is slow, down, or not needed)
# --------------------------------------------------------------------------
KW = {
    "WORRYING": ["help", "pain", "hurt", "fell", "fall", "fallen", "dizzy", "sick", "unwell", "emergency",
                 "chest", "breathe", "breathing", "ambulance", "bleeding", "not well", "not feeling well",
                 "मदद", "दर्द", "गिर", "चक्कर", "तबीयत", "बीमार", "madad", "dard", "chakkar",
                 "ಸಹಾಯ", "ನೋವು", "ಬಿದ್ದೆ", "ತಲೆ ಸುತ್ತು", "ಹುಷಾರಿಲ್ಲ", "sahaya", "novu"],
    "LATER": ["later", "call back", "call me back", "not now", "busy", "not yet", "minute", "minutes",
              "hour", "remind me", "बाद में", "बाद", "थोड़ी देर", "मिनट", "अभी नहीं", "baad", "baad mein",
              "ನಂತರ", "ಆಮೇಲೆ", "ನಿಮಿಷ", "amele", "nantara"],
    "DONE": ["done", "yes", "yeah", "yep", "took", "taken", "finished", "completed", "already", "did it",
             "हाँ", "हां", "हो गया", "ले ली", "ले लिया", "कर लिया", "खा ली", "haan", "ho gaya", "le li", "kar liya",
             "ಹೌದು", "ಆಯ್ತು", "ಆಯಿತು", "ಮಾಡಿದೆ", "ತಗೊಂಡೆ", "haudu", "aaytu", "maadide"],
    "QUESTION": ["?", "what", "which", "when", "where", "how", "why", "can i", "should i", "who",
                 "क्या", "कौन", "कब", "कैसे", "कहाँ", "kya", "kaun", "kab", "kaise",
                 "ಯಾವ", "ಏನು", "ಯಾವಾಗ", "ಹೇಗೆ", "ಎಲ್ಲಿ", "yaava", "enu"],
}
CRITICAL_WORDS = ["medicine", "medication", "tablet", "pill", "insulin", "dose", "bp", "blood pressure", "sugar",
                  "inhaler", "injection", "health", "dawai", "दवा", "दवाई", "गोली", "ಔಷಧ", "ಮಾತ್ರೆ"]
HIGH_WORDS = ["appointment", "doctor", "hospital", "clinic", "checkup", "check-up", "test", "scan", "bank",
              "डॉक्टर", "अस्पताल", "ಡಾಕ್ಟರ್", "ಆಸ್ಪತ್ರೆ"]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45}


def _has(text: str, word: str) -> bool:
    if word.isascii() and word[0].isalnum():
        return re.search(r"\b" + re.escape(word) + r"\b", text) is not None
    return word in text


def keyword_outcome(text: str) -> str:
    t = text.lower().strip()
    if not t:
        return "UNCLEAR"
    for outcome in ("WORRYING", "LATER", "DONE", "QUESTION"):
        if any(_has(t, w) for w in KW[outcome]):
            return outcome
    return "UNCLEAR"


def extract_minutes(text: str):
    t = text.lower()
    m = re.search(r"(\d+)\s*(min|minute|minutes|मिनट|ನಿಮಿಷ)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*(hour|hours|घंटे|घंटा|ಗಂಟೆ)", t)
    if m:
        return int(m.group(1)) * 60
    if "half an hour" in t or "आधा घंटा" in t or "ಅರ್ಧ ಗಂಟೆ" in t:
        return 30
    for word, n in sorted(NUMBER_WORDS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{word}\s+minutes?\b", t):
            return n
    if re.search(r"\b(an|one) hour\b", t) or "एक घंटा" in t:
        return 60
    return None


def clamp_minutes(m) -> int:
    try:
        m = int(m)
    except Exception:
        m = 10
    return max(1, min(m, 240))


# --------------------------------------------------------------------------
# 1. Planner agent
# --------------------------------------------------------------------------
PLANNER_SYSTEM = (
    "You are the Planner agent of CareCall, a reminder phone-call service for elders. "
    "Decide the priority of a reminder: critical = medicine or health, high = appointments, normal = everything else. "
    'Return JSON: {"priority":"critical|high|normal","reason":"max 8 words","spoken_message":"..."}. '
    "spoken_message = the reminder rewritten for speaking, in the target language, same meaning, nothing added."
)


def planner_agent(message: str, language: str) -> dict:
    lang_name = LANGS.get(language, LANGS["en"])["name"]
    try:
        out = ask_json(PLANNER_SYSTEM, f"Target language: {lang_name}\nReminder: {message}", max_tokens=250, timeout=8)
        priority = str(out.get("priority", "normal")).lower()
        if priority not in PLANS:
            priority = "normal"
        spoken = str(out.get("spoken_message") or message).strip()
        if language == "en":
            spoken = message  # keep the sender's exact words in English
        return {"priority": priority, "reason": str(out.get("reason", ""))[:80], "spoken_message": spoken,
                "source": "llm", **plan_for(priority)}
    except Exception as e:
        t = message.lower()
        if any(_has(t, w) for w in CRITICAL_WORDS):
            priority = "critical"
        elif any(_has(t, w) for w in HIGH_WORDS):
            priority = "high"
        else:
            priority = "normal"
        return {"priority": priority, "reason": "keyword rule", "spoken_message": message,
                "source": f"fallback ({type(e).__name__})", **plan_for(priority)}


# --------------------------------------------------------------------------
# 2. Listener agent
# --------------------------------------------------------------------------
LISTENER_SYSTEM = (
    "You are the Listener agent on a reminder phone call. Classify what the person said into exactly one outcome: "
    "DONE (they did it / yes), LATER (call back later; give minutes, default 10), "
    "QUESTION (they asked something), WORRYING (pain, fall, unwell, confused, needs help, emergency), "
    "UNCLEAR (anything else). For QUESTION, reply = a short answer using ONLY facts in the sender's note; "
    "if the note does not contain the answer, reply exactly: NOINFO. Never give medical advice. "
    'Return JSON: {"outcome":"DONE|LATER|QUESTION|WORRYING|UNCLEAR","minutes":null,"reply":"one short sentence"}. '
    "Write reply in the call language."
)


def listener_agent(call: dict, lang: str, speech: str, digits: str) -> dict:
    """Returns {outcome, minutes, reply, source}."""
    digits = (digits or "").strip()
    if digits:
        mapping = {"1": "DONE", "2": "LATER", "3": "WORRYING"}
        return {"outcome": mapping.get(digits[0], "UNCLEAR"), "minutes": 10, "reply": "", "source": "keypad"}

    speech = (speech or "").strip()
    if not speech:
        return {"outcome": "UNCLEAR", "minutes": None, "reply": "", "source": "empty"}

    note = call.get("note") or call.get("message")
    user = (f"Call language: {LANGS[lang]['name']}\nReminder: {call['message']}\n"
            f"Sender's note: {note}\nPerson said: \"{speech}\"")
    try:
        out = ask_json(LISTENER_SYSTEM, user, max_tokens=120)
        outcome = str(out.get("outcome", "UNCLEAR")).upper()
        if outcome not in ("DONE", "LATER", "QUESTION", "WORRYING", "UNCLEAR"):
            outcome = "UNCLEAR"
        # safety net: never let the LLM miss a worrying word
        if keyword_outcome(speech) == "WORRYING":
            outcome = "WORRYING"
        reply = str(out.get("reply") or "").strip()
        if "NOINFO" in reply.upper() or not reply:
            reply = p(lang, "noinfo")
        minutes = extract_minutes(speech) or out.get("minutes")
        return {"outcome": outcome, "minutes": minutes, "reply": reply, "source": "llm"}
    except Exception as e:
        outcome = keyword_outcome(speech)
        return {"outcome": outcome, "minutes": extract_minutes(speech), "reply": p(lang, "noinfo"),
                "source": f"keyword fallback ({type(e).__name__})"}


# --------------------------------------------------------------------------
# 3. Guardian agent
# --------------------------------------------------------------------------
GUARDIAN_SYSTEM = (
    "You are the Guardian safety agent. Check a proposed answer that will be spoken to an elder. "
    "Approve ONLY if: every fact in it appears in the sender's note, it gives no medical advice "
    "beyond repeating the note, and it invents nothing. "
    'Return JSON: {"ok":true|false,"reason":"max 10 words"}.'
)


def guardian_agent(call: dict, lang: str, question: str, answer: str) -> dict:
    """Returns {ok, final_answer, reason, source}."""
    safe = p(lang, "noinfo")
    if answer.strip() == safe:
        return {"ok": True, "final_answer": safe, "reason": "safe 'no information' reply", "source": "rule"}
    note = call.get("note") or call.get("message")
    try:
        out = ask_json(GUARDIAN_SYSTEM, f"Sender's note: {note}\nQuestion: {question}\nProposed answer: {answer}",
                       max_tokens=60, timeout=2.0)
        ok = out.get("ok") is True or str(out.get("ok")).lower() == "true"
        return {"ok": ok, "final_answer": answer if ok else safe, "reason": str(out.get("reason", ""))[:100],
                "source": "llm"}
    except Exception as e:
        # fail closed: if we cannot check it, we do not say it
        return {"ok": False, "final_answer": safe, "reason": f"could not verify ({type(e).__name__})",
                "source": "fallback"}


# --------------------------------------------------------------------------
# 4. Reporter agent
# --------------------------------------------------------------------------
REPORTER_SYSTEM = (
    "You are the Reporter agent. Write ONE plain sentence (max 25 words) for the family member who sent a reminder, "
    "saying what happened on the call. Use only the transcript. If status is needs_attention start with 'ATTENTION:'. "
    'Return JSON: {"summary":"..."}'
)


def _fallback_summary(call: dict) -> str:
    s = call["status"]
    a = call.get("attempts", 0)
    return {
        "confirmed": "They confirmed the reminder was done.",
        "rescheduled": "They asked to be called later; a new call is scheduled.",
        "retrying": f"No clear answer yet after {a} attempt(s); retrying soon.",
        "needs_attention": "ATTENTION: they said something worrying on the call. Please check on them.",
        "unreachable": f"Could not reach them after {a} attempt(s).",
        "calling": "The call is in progress.",
        "scheduled": "The call is scheduled.",
    }.get(s, f"Status: {s}.")


def reporter_agent(call: dict, events: list) -> str:
    lines = [f"{e['agent']}: {e['text']}" for e in events[-25:]]
    user = (f"Reminder: {call['message']}\nStatus: {call['status']}\nAttempts: {call.get('attempts', 0)}\n"
            "Transcript:\n" + "\n".join(lines))
    try:
        out = ask_json(REPORTER_SYSTEM, user, max_tokens=80, timeout=8)
        summary = str(out.get("summary", "")).strip()
        if not summary:
            raise ValueError("empty summary")
        if call["status"] == "needs_attention" and not summary.upper().startswith("ATTENTION"):
            summary = "ATTENTION: " + summary
        words = summary.split()
        if len(words) > 25:
            summary = " ".join(words[:25]).rstrip(",;.") + "."
        return summary
    except Exception:
        return _fallback_summary(call)
```

### 4.10 `backend/telephony.py`

Twilio: place calls, build TwiML.

```python
"""Twilio Voice: place calls and build TwiML (the XML that tells Twilio what to say / listen for)."""
from urllib.parse import urlencode

from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

import config
from phrases import LANGS

RING_SECONDS = 20  # how long the phone rings before Twilio gives up ("no-answer")


def place_call(call: dict, attempt: int) -> str:
    """Start an outbound call. Returns the Twilio Call SID (or a fake one in DRY_RUN)."""
    base = config.get_public_url()
    qs = urlencode({"call_id": call["id"], "attempt": attempt})
    if config.DRY_RUN:
        print(f"\n[DRY_RUN] Would call {call['phone']} (attempt {attempt}).")
        print(f"[DRY_RUN] Simulate it:  python simulate_call.py {call['id']} \"yes I took it\"\n")
        return f"DRYRUN-{attempt}"
    if not base.startswith("https://"):
        raise RuntimeError("PUBLIC_URL is not set to the https ngrok address")
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    c = client.calls.create(
        to=call["phone"],
        from_=config.TWILIO_FROM_NUMBER,
        url=f"{base}/twilio/voice?{qs}",
        method="POST",
        status_callback=f"{base}/twilio/status?{qs}",
        status_callback_event=["completed"],
        status_callback_method="POST",
        timeout=RING_SECONDS,
    )
    return c.sid


def _say(target, text: str, lang: str):
    target.say(text, voice=config.VOICES.get(lang, config.VOICES["en"]), language=LANGS[lang]["code"])


def gather_twiml(lang: str, texts: list[str], action_url: str, noinput_url: str) -> str:
    """Speak texts, then listen for speech or one keypad digit."""
    vr = VoiceResponse()
    g = Gather(
        input="speech dtmf",
        num_digits=1,
        action=action_url,
        method="POST",
        language=LANGS[lang]["code"],
        speech_timeout="auto",
        timeout=6,
        hints="done, yes, later, minutes, help, call me later",
    )
    for t in texts:
        if t:
            _say(g, t, lang)
    vr.append(g)
    vr.redirect(noinput_url, method="POST")  # reached only if the person says nothing
    return str(vr)


def hangup_twiml(lang: str, texts: list[str]) -> str:
    vr = VoiceResponse()
    for t in texts:
        if t:
            _say(vr, t, lang)
    vr.hangup()
    return str(vr)
```

### 4.11 `backend/scheduler.py`

APScheduler: fire, retry, reschedule, restore.

```python
"""APScheduler inside the backend: fires calls, reschedules, retries, restores after restart."""
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

import telephony
from db import PENDING, db

sched = BackgroundScheduler(timezone="UTC")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def start():
    if not sched.running:
        sched.start()
    restore_pending()


def shutdown():
    if sched.running:
        sched.shutdown(wait=False)


def schedule_attempt(call_id: str, when: datetime):
    when = max(when, utcnow() + timedelta(seconds=2))
    sched.add_job(fire_call, DateTrigger(run_date=when.astimezone(timezone.utc), timezone=timezone.utc), args=[call_id], id=f"call-{call_id}",
                  replace_existing=True, misfire_grace_time=600)


def cancel_all():
    sched.remove_all_jobs()


def restore_pending():
    """After a restart, put every pending call back on the scheduler."""
    for c in db.list_pending():
        when = parse_dt(c.get("next_attempt_at") or c["scheduled_at"])
        if when < utcnow():
            when = utcnow() + timedelta(seconds=5)
        schedule_attempt(c["id"], when)
        db.add_event(c["id"], "SCHEDULER", f"Restored after restart; will call at {when.astimezone():%H:%M:%S}.")
        print(f"[scheduler] restored call {c['id']} for {when.isoformat()}")


def fire_call(call_id: str):
    """Runs at the scheduled time: places the call through Twilio."""
    c = db.get_call(call_id)
    if not c or c["status"] not in PENDING:
        return
    attempt = int(c.get("attempts") or 0) + 1
    db.update_call(call_id, status="calling", attempts=attempt)
    db.add_event(call_id, "SCHEDULER", f"Attempt {attempt} of plan: calling {c['phone']}.")
    try:
        sid = telephony.place_call(c, attempt)
        db.update_call(call_id, twilio_sid=sid)
        db.add_event(call_id, "TWILIO", f"Ringing (call {sid}).")
    except Exception as e:
        db.add_event(call_id, "SYSTEM", f"Could not start call: {str(e)[:150]}")
        handle_failure(call_id, "call could not be placed")


def handle_failure(call_id: str, reason: str):
    """No answer / unclear / hung up: retry by the Planner's plan, or mark unreachable."""
    c = db.get_call(call_id)
    if not c:
        return
    failed = int(c.get("failed_attempts") or 0) + 1
    max_attempts = int(c.get("max_attempts") or 2)
    if failed >= max_attempts:
        db.update_call(call_id, status="unreachable", failed_attempts=failed, next_attempt_at=None)
        db.add_event(call_id, "SCHEDULER", f"{reason}. No success after {failed} attempt(s): marked UNREACHABLE.")
        return
    gap = int(c.get("retry_gap_seconds") or 600)
    when = utcnow() + timedelta(seconds=gap)
    db.update_call(call_id, status="retrying", failed_attempts=failed, next_attempt_at=when.isoformat())
    schedule_attempt(call_id, when)
    db.add_event(call_id, "SCHEDULER",
                 f"{reason}. Retry {failed + 1} of {max_attempts} in {gap} s ({c['priority']} priority plan).")


def reschedule(call_id: str, minutes: int):
    when = utcnow() + timedelta(minutes=minutes)
    db.update_call(call_id, status="rescheduled", next_attempt_at=when.isoformat())
    schedule_attempt(call_id, when)
    db.add_event(call_id, "SCHEDULER", f"Rescheduled: will call again in {minutes} min (at {when.astimezone():%H:%M}).")
```

### 4.12 `backend/main.py`

FastAPI app: dashboard API + Twilio webhooks.

```python
"""CareCall backend: FastAPI app.

Run:  uvicorn main:app --port 8000
  /api/...     used by the v0 dashboard (or the fallback page at /)
  /twilio/...  webhooks Twilio calls during a phone call (through ngrok)
"""
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import config
import scheduler
from agents import clamp_minutes, guardian_agent, listener_agent, planner_agent, reporter_agent
from db import BACKEND_NAME, db, now_iso
from phrases import LANGS, lang_of, p
from telephony import gather_twiml, hangup_twiml

MAX_QUESTIONS = 2  # questions answered per call
MAX_UNCLEAR = 1    # re-asks after an unclear reply before giving up on this attempt


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    print(f"[carecall] DRY_RUN={config.DRY_RUN} DEMO_MODE={config.DEMO_MODE} DB={BACKEND_NAME} "
          f"LLM={config.LLM_PROVIDER} PUBLIC_URL={config.get_public_url() or '(not set)'}")
    yield
    scheduler.shutdown()


app = FastAPI(title="CareCall", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def xml(twiml: str) -> Response:
    return Response(content=twiml, media_type="application/xml")


# ==========================================================================
# Dashboard API
# ==========================================================================
class CallIn(BaseModel):
    phone: str
    message: str
    language: str = "en"
    scheduled_at: str            # ISO time, e.g. 2026-11-01T10:30:00.000Z
    note: str = ""               # extra facts the AI may use to answer questions
    sender_name: str = ""


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "dry_run": config.DRY_RUN, "demo_mode": config.DEMO_MODE, "db": BACKEND_NAME,
            "llm": config.LLM_PROVIDER, "public_url": config.get_public_url(),
            "languages": config.ENABLED_LANGUAGES, "jobs": len(scheduler.sched.get_jobs())}


@app.post("/api/calls")
def create_call(body: CallIn):
    phone = re.sub(r"[\s\-()]", "", body.phone)
    if not re.fullmatch(r"\+\d{8,15}", phone):
        raise HTTPException(400, "Phone must be in international format, e.g. +919876543210")
    if not body.message.strip():
        raise HTTPException(400, "Message is empty")
    language = body.language if body.language in LANGS else "en"
    try:
        when = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "scheduled_at must be an ISO date-time")
    if when.tzinfo is None:
        when = when.astimezone()  # treat as this laptop's local time
    when = max(when, scheduler.utcnow() + timedelta(seconds=5))

    plan = planner_agent(body.message.strip(), language)
    call_id = str(uuid.uuid4())
    row = {
        "id": call_id,
        "phone": phone,
        "message": body.message.strip(),
        "note": body.note.strip(),
        "sender_name": body.sender_name.strip(),
        "language": language,
        "spoken_message": plan["spoken_message"],
        "priority": plan["priority"],
        "priority_reason": plan["reason"],
        "max_attempts": plan["max_attempts"],
        "retry_gap_seconds": plan["retry_gap_seconds"],
        "attempts": 0,
        "failed_attempts": 0,
        "status": "scheduled",
        "scheduled_at": when.isoformat(),
        "next_attempt_at": when.isoformat(),
        "summary": "",
        "twilio_sid": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    db.insert_call(row)
    db.add_event(call_id, "PLANNER",
                 f"Priority {plan['priority'].upper()} ({plan['reason']}). Plan: up to {plan['max_attempts']} "
                 f"attempts, {plan['retry_gap_seconds']} s apart. [{plan['source']}]")
    if lang_of(row) != language:
        db.add_event(call_id, "SYSTEM", f"{LANGS[language]['name']} is disabled; this call will use English.")
    scheduler.schedule_attempt(call_id, when)
    db.add_event(call_id, "SCHEDULER", f"Queued for {when.astimezone():%d %b %H:%M:%S}.")
    return db.get_call(call_id)


@app.get("/api/calls")
def list_calls():
    calls = db.list_calls()
    events = db.list_events([c["id"] for c in calls])
    by_call = {}
    for e in events:
        by_call.setdefault(e["call_id"], []).append(e)
    for c in calls:
        c["events"] = by_call.get(c["id"], [])
    return calls


@app.post("/api/calls/{call_id}/call-now")
def call_now(call_id: str):
    c = db.get_call(call_id)
    if not c:
        raise HTTPException(404, "Not found")
    if c["status"] not in ("scheduled", "rescheduled", "retrying"):
        db.update_call(call_id, status="scheduled", failed_attempts=0)
    scheduler.schedule_attempt(call_id, scheduler.utcnow())
    db.add_event(call_id, "SCHEDULER", "Manual 'call now'.")
    return {"ok": True}


@app.delete("/api/calls")
def reset_all():
    scheduler.cancel_all()
    db.reset()
    return {"ok": True}


# ==========================================================================
# Twilio webhooks (the live call)
# ==========================================================================
def _url(path: str, **params) -> str:
    # Relative URLs work: Twilio resolves them against the current webhook address.
    return f"{path}?{urlencode(params)}"


def _listen(call: dict, lang: str, texts: list, attempt: int, q: int, u: int) -> Response:
    params = dict(call_id=call["id"], attempt=attempt, q=q, u=u)
    return xml(gather_twiml(lang, texts, _url("/twilio/gather", **params), _url("/twilio/noinput", **params)))


@app.post("/twilio/voice")
async def twilio_voice(call_id: str, attempt: int = 1):
    """Person answered: speak the reminder and ask 'Did you do it?'."""
    c = db.get_call(call_id)
    if not c:
        return xml(hangup_twiml("en", ["Sorry, this reminder no longer exists. Goodbye."]))
    lang = lang_of(c)
    sender = c.get("sender_name") or p(lang, "family")
    intro = p(lang, "intro", sender=sender)
    db.add_event(call_id, "TWILIO", "Call answered.")
    db.add_event(call_id, "CARECALL", f"{intro} {c['spoken_message']}")
    return _listen(c, lang, [intro, c["spoken_message"], p(lang, "ask")], attempt, 0, 0)


@app.post("/twilio/gather")
async def twilio_gather(request: Request, call_id: str, attempt: int = 1, q: int = 0, u: int = 0):
    """Person replied (speech or keypad): Listener decides, system acts."""
    form = await request.form()
    speech = str(form.get("SpeechResult") or "")
    digits = str(form.get("Digits") or "")
    c = db.get_call(call_id)
    if not c:
        return xml(hangup_twiml("en", ["Goodbye."]))
    lang = lang_of(c)

    db.add_event(call_id, "PERSON", f"pressed {digits}" if digits else f"\"{speech}\"")
    res = listener_agent(c, lang, speech, digits)
    detail = f" {clamp_minutes(res['minutes'])} min" if res["outcome"] == "LATER" else ""
    db.add_event(call_id, "LISTENER", f"{res['outcome']}{detail} [{res['source']}]")
    outcome = res["outcome"]

    if outcome == "DONE":
        db.update_call(call_id, status="confirmed", next_attempt_at=None)
        db.add_event(call_id, "CARECALL", p(lang, "done"))
        return xml(hangup_twiml(lang, [p(lang, "done")]))

    if outcome == "LATER":
        minutes = clamp_minutes(res["minutes"])
        scheduler.reschedule(call_id, minutes)
        text = p(lang, "later", m=minutes)
        db.add_event(call_id, "CARECALL", text)
        return xml(hangup_twiml(lang, [text]))

    if outcome == "WORRYING":
        db.update_call(call_id, status="needs_attention", next_attempt_at=None)
        db.add_event(call_id, "SYSTEM", "Marked NEEDS ATTENTION. No escalation; the sender will see this.")
        db.add_event(call_id, "CARECALL", p(lang, "worrying"))
        return xml(hangup_twiml(lang, [p(lang, "worrying")]))

    if outcome == "QUESTION" and q < MAX_QUESTIONS:
        verdict = guardian_agent(c, lang, speech, res["reply"])
        if verdict["ok"]:
            db.add_event(call_id, "GUARDIAN", f"PASSED: {verdict['reason']} [{verdict['source']}]")
        else:
            db.add_event(call_id, "GUARDIAN",
                         f"BLOCKED \"{res['reply']}\": {verdict['reason']} [{verdict['source']}]")
        answer = verdict["final_answer"]
        db.add_event(call_id, "CARECALL", answer)
        return _listen(c, lang, [answer, p(lang, "reask")], attempt, q + 1, u)

    # UNCLEAR, or too many questions
    return _unclear(c, lang, attempt, q, u, "Unclear reply" if outcome == "UNCLEAR" else "Too many questions")


@app.post("/twilio/noinput")
async def twilio_noinput(call_id: str, attempt: int = 1, q: int = 0, u: int = 0):
    """Person said nothing."""
    c = db.get_call(call_id)
    if not c:
        return xml(hangup_twiml("en", ["Goodbye."]))
    db.add_event(call_id, "PERSON", "(silence)")
    return _unclear(c, lang_of(c), attempt, q, u, "No reply")


def _unclear(c: dict, lang: str, attempt: int, q: int, u: int, reason: str) -> Response:
    if u < MAX_UNCLEAR:
        db.add_event(c["id"], "CARECALL", p(lang, "unclear"))
        return _listen(c, lang, [p(lang, "unclear")], attempt, q, u + 1)
    db.add_event(c["id"], "CARECALL", p(lang, "bye_unclear"))
    scheduler.handle_failure(c["id"], reason)
    return xml(hangup_twiml(lang, [p(lang, "bye_unclear")]))


@app.post("/twilio/status")
async def twilio_status(request: Request, background: BackgroundTasks, call_id: str, attempt: int = 1):
    """Twilio says the call ended (completed, no-answer, busy, failed, canceled)."""
    form = await request.form()
    status = str(form.get("CallStatus") or "completed")
    duration = str(form.get("CallDuration") or "0")
    c = db.get_call(call_id)
    if not c:
        return {"ok": True}
    if int(c.get("attempts") or 0) != attempt:
        return {"ok": True, "ignored": "old attempt"}
    db.add_event(call_id, "TWILIO", f"Call ended: {status} ({duration} s).")
    if c["status"] == "calling":
        # nobody gave a final answer on this attempt
        scheduler.handle_failure(call_id, {"no-answer": "No answer", "busy": "Busy / declined",
                                           "failed": "Call failed", "canceled": "Call canceled"}
                                 .get(status, "Hung up without answering"))
    background.add_task(run_reporter, call_id)
    return {"ok": True}


def run_reporter(call_id: str):
    c = db.get_call(call_id)
    if not c:
        return
    summary = reporter_agent(c, db.list_events([call_id]))
    db.update_call(call_id, summary=summary)
    db.add_event(call_id, "REPORTER", summary)
```

### 4.13 `backend/hello_call.py`

Hour 0–1 test call and voice test.

```python
"""Hour 0-1 test: place one real call that just speaks. No ngrok or backend needed.

Usage (PowerShell, inside backend folder with the venv active):
  python hello_call.py +919876543210          # English
  python hello_call.py +919876543210 hi       # Hindi voice test
  python hello_call.py +919876543210 kn       # Kannada voice test
"""
import sys

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

import config
from phrases import LANGS

TEXT = {
    "en": "Hello from CareCall. This is team Carpe Diem. If you can hear this, the call works.",
    "hi": "नमस्ते, यह केयरकॉल है। अगर आप यह सुन पा रहे हैं, तो कॉल ठीक से काम कर रहा है।",
    "kn": "ನಮಸ್ಕಾರ, ಇದು ಕೇರ್‌ಕಾಲ್. ನಿಮಗೆ ಇದು ಕೇಳಿಸುತ್ತಿದ್ದರೆ, ಕರೆ ಸರಿಯಾಗಿ ಕೆಲಸ ಮಾಡುತ್ತಿದೆ.",
}

if len(sys.argv) < 2:
    sys.exit("Usage: python hello_call.py +91XXXXXXXXXX [en|hi|kn]")
to = sys.argv[1]
lang = sys.argv[2] if len(sys.argv) > 2 else "en"

vr = VoiceResponse()
vr.say(TEXT[lang], voice=config.VOICES[lang], language=LANGS[lang]["code"])
vr.pause(length=1)
vr.say(TEXT[lang], voice=config.VOICES[lang], language=LANGS[lang]["code"])

client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
call = client.calls.create(to=to, from_=config.TWILIO_FROM_NUMBER, twiml=str(vr))
print("Call placed. SID:", call.sid)
print("Check: Twilio console > Monitor > Logs > Calls")
```

### 4.14 `backend/simulate_call.py`

Pretend to be Twilio + the person (DRY_RUN testing).

```python
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
```

### 4.15 `backend/static/index.html`

Fallback UI served by FastAPI at http://localhost:8000.

```html
<!doctype html>
<!-- FALLBACK UI: served by FastAPI at http://localhost:8000/ . Use it if the v0 page is not ready by hour 6. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CareCall</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1b1f24; --muted:#667085; --line:#e4e7ec; --brand:#0f766e; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, Segoe UI, Arial, sans-serif; background:var(--bg); color:var(--ink); }
  header { background:var(--brand); color:#fff; padding:14px 20px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
  header h1 { margin:0; font-size:20px; }
  header .sub { opacity:.85; font-size:13px; }
  header .right { margin-left:auto; display:flex; gap:8px; align-items:center; font-size:12px; }
  main { display:grid; grid-template-columns: 340px 1fr; gap:16px; padding:16px; max-width:1300px; margin:auto; }
  @media (max-width: 860px) { main { grid-template-columns: 1fr; } }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }
  label { display:block; font-size:13px; font-weight:600; margin:10px 0 4px; }
  input, textarea, select { width:100%; padding:9px; border:1px solid var(--line); border-radius:7px; font:inherit; }
  textarea { min-height:70px; }
  button { cursor:pointer; border:0; border-radius:7px; padding:10px 14px; font-weight:600; font:inherit; }
  .primary { background:var(--brand); color:#fff; width:100%; margin-top:14px; }
  .ghost { background:#fff; color:var(--ink); border:1px solid var(--line); padding:6px 10px; }
  .danger { background:#fee4e2; color:#b42318; padding:6px 10px; }
  .msg { font-size:13px; margin-top:8px; }
  .call { border:1px solid var(--line); border-radius:10px; margin-bottom:12px; background:#fff; }
  .call.attn { border:2px solid #d92d20; }
  .top { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:12px 14px; }
  .badge { font-size:12px; font-weight:700; padding:3px 9px; border-radius:99px; text-transform:uppercase; }
  .s-scheduled{background:#eef4ff;color:#3538cd} .s-calling{background:#fef0c7;color:#b54708}
  .s-confirmed{background:#dcfae6;color:#067647} .s-rescheduled{background:#e0f2fe;color:#026aa2}
  .s-retrying{background:#fdead7;color:#c4320a} .s-needs_attention{background:#d92d20;color:#fff}
  .s-unreachable{background:#e4e7ec;color:#344054}
  .p-critical{background:#fee4e2;color:#b42318} .p-high{background:#fef0c7;color:#b54708} .p-normal{background:#f2f4f7;color:#475467}
  .summary { padding:0 14px 10px; font-size:14px; }
  .summary.attn { color:#b42318; font-weight:700; }
  .meta { color:var(--muted); font-size:12px; }
  details { border-top:1px solid var(--line); padding:8px 14px; }
  summary { cursor:pointer; font-size:13px; color:var(--muted); }
  .line { display:flex; gap:8px; font-size:13px; padding:3px 0; }
  .tag { font-size:10px; font-weight:800; min-width:84px; text-align:center; padding:2px 6px; border-radius:5px; height:fit-content; }
  .a-PLANNER{background:#ede9fe;color:#5b21b6} .a-LISTENER{background:#dbeafe;color:#1e40af}
  .a-GUARDIAN{background:#fef3c7;color:#92400e} .a-REPORTER{background:#dcfce7;color:#166534}
  .a-SCHEDULER{background:#f1f5f9;color:#334155} .a-TWILIO{background:#fce7f3;color:#9d174d}
  .a-PERSON{background:#e0f2fe;color:#075985} .a-CARECALL{background:#ccfbf1;color:#115e59} .a-SYSTEM{background:#fee2e2;color:#991b1b}
  .time { color:var(--muted); font-size:11px; min-width:58px; }
</style>
</head>
<body>
<header>
  <h1>CareCall</h1><span class="sub">Multi-agent reminder calls · Team Carpe Diem (Ah-11)</span>
  <div class="right"><span id="health">checking…</span><button class="danger" onclick="resetAll()">Reset demo</button></div>
</header>
<main>
  <section class="card">
    <h2 style="margin:0 0 4px;font-size:17px">Schedule a call</h2>
    <form id="f">
      <label>Phone number (international)</label>
      <input name="phone" placeholder="+919876543210" required>
      <label>Message to speak</label>
      <textarea name="message" placeholder="Please take your blood pressure tablet after breakfast." required></textarea>
      <label>Note for questions (optional)</label>
      <textarea name="note" placeholder="Blue tablet, the box is on the kitchen shelf. Take it with water."></textarea>
      <label>Your name (optional)</label>
      <input name="sender_name" placeholder="Ravi">
      <label>Date and time</label>
      <input name="when" type="datetime-local" required>
      <label>Language</label>
      <select name="language"><option value="en">English</option><option value="hi">Hindi</option><option value="kn">Kannada</option></select>
      <button class="primary" type="submit">Schedule call</button>
      <div class="msg" id="formMsg"></div>
    </form>
  </section>
  <section>
    <div id="list"><div class="card meta">No calls yet.</div></div>
  </section>
</main>
<script>
const API = "";  // same origin as FastAPI
const H = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };
const STATUS_LABEL = { needs_attention: "needs attention" };
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
const t = iso => iso ? new Date(iso).toLocaleTimeString([], { hour:"2-digit", minute:"2-digit", second:"2-digit" }) : "";

function defaultWhen() {
  const d = new Date(Date.now() + 60_000); d.setSeconds(0, 0);
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
document.querySelector("[name=when]").value = defaultWhen();

document.getElementById("f").addEventListener("submit", async e => {
  e.preventDefault();
  const fd = new FormData(e.target), msg = document.getElementById("formMsg");
  msg.textContent = "Planner agent is thinking…";
  try {
    const r = await fetch(API + "/api/calls", { method: "POST", headers: H, body: JSON.stringify({
      phone: fd.get("phone"), message: fd.get("message"), note: fd.get("note"),
      sender_name: fd.get("sender_name"), language: fd.get("language"),
      scheduled_at: new Date(fd.get("when")).toISOString() }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.status);
    msg.textContent = `Scheduled. Priority: ${data.priority}.`;
    document.querySelector("[name=when]").value = defaultWhen();
    load();
  } catch (err) { msg.textContent = "Error: " + err.message; }
});

async function resetAll() {
  if (!confirm("Delete all calls?")) return;
  await fetch(API + "/api/calls", { method: "DELETE", headers: H }); load();
}
async function callNow(id) { await fetch(API + `/api/calls/${id}/call-now`, { method: "POST", headers: H }); load(); }

const openIds = new Set();
function render(calls) {
  const list = document.getElementById("list");
  list.querySelectorAll("details[open]").forEach(d => openIds.add(d.dataset.id));
  list.querySelectorAll("details:not([open])").forEach(d => openIds.delete(d.dataset.id));
  if (!calls.length) { list.innerHTML = '<div class="card meta">No calls yet.</div>'; return; }
  list.innerHTML = calls.map(c => {
    const attn = c.status === "needs_attention";
    return `<div class="call ${attn ? "attn" : ""}">
      <div class="top">
        <span class="badge s-${c.status}">${esc(STATUS_LABEL[c.status] || c.status)}</span>
        <span class="badge p-${c.priority}">${esc(c.priority)}</span>
        <b>${esc(c.phone)}</b>
        <span class="meta">${esc(c.language.toUpperCase())} · ${new Date(c.scheduled_at).toLocaleString()} · attempts ${c.attempts}/${c.max_attempts}+</span>
        <button class="ghost" style="margin-left:auto" onclick="callNow('${c.id}')">Call now</button>
      </div>
      <div class="summary meta">“${esc(c.message)}”</div>
      ${c.summary ? `<div class="summary ${attn ? "attn" : ""}">Reporter: ${esc(c.summary)}</div>` : ""}
      <details data-id="${c.id}" ${openIds.has(c.id) ? "open" : ""}>
        <summary>Transcript (${c.events.length} lines)</summary>
        ${c.events.map(e => `<div class="line"><span class="time">${t(e.created_at)}</span>
          <span class="tag a-${e.agent}">${esc(e.agent)}</span><span>${esc(e.text)}</span></div>`).join("")}
      </details></div>`;
  }).join("");
}

async function load() {
  try {
    const r = await fetch(API + "/api/calls", { headers: H });
    render(await r.json());
  } catch (e) { /* backend restarting; try again next tick */ }
}
async function health() {
  try {
    const h = await (await fetch(API + "/api/health", { headers: H })).json();
    document.getElementById("health").textContent =
      `${h.dry_run ? "DRY RUN" : "LIVE CALLS"} · db ${h.db} · llm ${h.llm}${h.demo_mode ? " · demo mode" : ""}`;
  } catch { document.getElementById("health").textContent = "backend offline"; }
}
load(); health(); setInterval(load, 2000); setInterval(health, 10000);
</script>
</body>
</html>
```

### 4.16 `frontend/v0_prompt.md`

Prompt for v0.

```markdown
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
```

### 4.17 `frontend/page.tsx`

Reference React page for v0 (app/page.tsx).

```tsx
"use client";
// CareCall dashboard for v0 (Next.js App Router + Tailwind). No other libraries needed.
// Paste into v0 as app/page.tsx if the generated page drifts from the API contract.
import { useCallback, useEffect, useRef, useState } from "react";

type Ev = { id: number; agent: string; text: string; created_at: string };
type Call = {
  id: string; phone: string; message: string; language: string; priority: string; status: string;
  scheduled_at: string; attempts: number; max_attempts: number; summary: string; events: Ev[];
};

const STATUS: Record<string, string> = {
  scheduled: "bg-indigo-100 text-indigo-800",
  calling: "bg-amber-100 text-amber-800",
  confirmed: "bg-green-100 text-green-800",
  rescheduled: "bg-sky-100 text-sky-800",
  retrying: "bg-orange-100 text-orange-800",
  needs_attention: "bg-red-600 text-white",
  unreachable: "bg-gray-200 text-gray-700",
};
const PRIORITY: Record<string, string> = {
  critical: "bg-red-100 text-red-800",
  high: "bg-amber-100 text-amber-800",
  normal: "bg-gray-100 text-gray-700",
};
const AGENT: Record<string, string> = {
  PLANNER: "bg-violet-100 text-violet-800",
  LISTENER: "bg-blue-100 text-blue-800",
  GUARDIAN: "bg-yellow-100 text-yellow-900",
  REPORTER: "bg-green-100 text-green-800",
  SCHEDULER: "bg-slate-100 text-slate-700",
  TWILIO: "bg-pink-100 text-pink-800",
  PERSON: "bg-sky-100 text-sky-800",
  CARECALL: "bg-teal-100 text-teal-800",
  SYSTEM: "bg-red-100 text-red-800",
};

function defaultWhen() {
  const d = new Date(Date.now() + 60_000);
  d.setSeconds(0, 0);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function Page() {
  const [api, setApi] = useState("");
  const [calls, setCalls] = useState<Call[]>([]);
  const [health, setHealth] = useState("not connected");
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [form, setForm] = useState({ phone: "", message: "", note: "", sender_name: "", language: "en", when: "" });
  const apiRef = useRef("");

  useEffect(() => {
    let saved = "";
    try { saved = localStorage.getItem("carecall_api") || ""; } catch {}
    setApi(saved); apiRef.current = saved;
    setForm(f => ({ ...f, when: defaultWhen() }));
  }, []);

  const headers = { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" };

  const load = useCallback(async () => {
    const base = apiRef.current;
    if (!base) return;
    try {
      const r = await fetch(`${base}/api/calls`, { headers });
      if (r.ok) setCalls(await r.json());
      const h = await (await fetch(`${base}/api/health`, { headers })).json();
      setHealth(`${h.dry_run ? "DRY RUN" : "LIVE CALLS"} · db ${h.db} · llm ${h.llm}`);
    } catch {
      setHealth("backend offline");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 2000); // live updates by polling every 2 seconds
    return () => clearInterval(t);
  }, [load]);

  function saveApi(v: string) {
    const clean = v.trim().replace(/\/+$/, "");
    setApi(clean); apiRef.current = clean;
    try { localStorage.setItem("carecall_api", clean); } catch {}
    load();
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg("Planner agent is thinking…");
    try {
      const r = await fetch(`${api}/api/calls`, {
        method: "POST", headers,
        body: JSON.stringify({
          phone: form.phone, message: form.message, note: form.note, sender_name: form.sender_name,
          language: form.language, scheduled_at: new Date(form.when).toISOString(),
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || String(r.status));
      setMsg(`Scheduled. Priority: ${data.priority}.`);
      setForm(f => ({ ...f, when: defaultWhen() }));
      load();
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
  }

  async function reset() {
    if (!confirm("Delete all calls?")) return;
    await fetch(`${api}/api/calls`, { method: "DELETE", headers });
    load();
  }
  async function callNow(id: string) {
    await fetch(`${api}/api/calls/${id}/call-now`, { method: "POST", headers });
    load();
  }

  const input = "w-full rounded-md border border-gray-300 px-3 py-2 text-sm";
  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="flex flex-wrap items-center gap-3 bg-teal-700 px-5 py-3 text-white">
        <h1 className="text-xl font-bold">CareCall</h1>
        <span className="text-sm opacity-80">Multi-agent reminder calls · Team Carpe Diem (Ah-11)</span>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <input className="w-72 rounded px-2 py-1 text-gray-900" placeholder="Backend URL (https://xxxx.ngrok-free.app)"
            defaultValue={api} key={api} onBlur={e => saveApi(e.target.value)} />
          <span>{health}</span>
          <button onClick={reset} className="rounded bg-red-100 px-3 py-1 font-semibold text-red-700">Reset demo</button>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-4 p-4 md:grid-cols-[340px_1fr]">
        <form onSubmit={submit} className="h-fit space-y-2 rounded-xl border bg-white p-4">
          <h2 className="font-semibold">Schedule a call</h2>
          <label className="block text-sm font-medium">Phone number (international)</label>
          <input className={input} required placeholder="+919876543210" value={form.phone}
            onChange={e => setForm({ ...form, phone: e.target.value })} />
          <label className="block text-sm font-medium">Message to speak</label>
          <textarea className={input} required rows={3} value={form.message}
            placeholder="Please take your blood pressure tablet after breakfast."
            onChange={e => setForm({ ...form, message: e.target.value })} />
          <label className="block text-sm font-medium">Note for questions (optional)</label>
          <textarea className={input} rows={2} value={form.note}
            placeholder="Blue tablet, the box is on the kitchen shelf. Take it with water."
            onChange={e => setForm({ ...form, note: e.target.value })} />
          <label className="block text-sm font-medium">Your name (optional)</label>
          <input className={input} value={form.sender_name} placeholder="Ravi"
            onChange={e => setForm({ ...form, sender_name: e.target.value })} />
          <label className="block text-sm font-medium">Date and time</label>
          <input className={input} type="datetime-local" required value={form.when}
            onChange={e => setForm({ ...form, when: e.target.value })} />
          <label className="block text-sm font-medium">Language</label>
          <select className={input} value={form.language} onChange={e => setForm({ ...form, language: e.target.value })}>
            <option value="en">English</option><option value="hi">Hindi</option><option value="kn">Kannada</option>
          </select>
          <button className="mt-2 w-full rounded-md bg-teal-700 py-2 font-semibold text-white" type="submit">Schedule call</button>
          <p className="text-sm text-gray-600">{msg}</p>
        </form>

        <section className="space-y-3">
          {calls.length === 0 && <div className="rounded-xl border bg-white p-4 text-sm text-gray-500">No calls yet.</div>}
          {calls.map(c => {
            const attn = c.status === "needs_attention";
            return (
              <div key={c.id} className={`rounded-xl bg-white ${attn ? "border-2 border-red-600" : "border"}`}>
                <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${STATUS[c.status] || ""}`}>
                    {c.status.replace("_", " ")}
                  </span>
                  <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${PRIORITY[c.priority] || ""}`}>
                    {c.priority}
                  </span>
                  <b>{c.phone}</b>
                  <span className="text-xs text-gray-500">
                    {c.language.toUpperCase()} · {new Date(c.scheduled_at).toLocaleString()} · attempts {c.attempts}
                  </span>
                  <button onClick={() => callNow(c.id)} className="ml-auto rounded border px-2 py-1 text-xs">Call now</button>
                </div>
                <p className="px-4 pb-2 text-sm text-gray-500">“{c.message}”</p>
                {c.summary && (
                  <p className={`px-4 pb-3 text-sm ${attn ? "font-bold text-red-700" : ""}`}>Reporter: {c.summary}</p>
                )}
                <div className="border-t px-4 py-2">
                  <button className="text-sm text-gray-500" onClick={() => setOpen(o => ({ ...o, [c.id]: !o[c.id] }))}>
                    {open[c.id] ? "▾" : "▸"} Transcript ({c.events.length} lines)
                  </button>
                  {open[c.id] && (
                    <div className="mt-2 space-y-1">
                      {c.events.map(ev => (
                        <div key={ev.id} className="flex gap-2 text-sm">
                          <span className="w-16 shrink-0 text-xs text-gray-400">
                            {new Date(ev.created_at).toLocaleTimeString()}
                          </span>
                          <span className={`h-fit w-24 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-extrabold ${AGENT[ev.agent] || ""}`}>
                            {ev.agent}
                          </span>
                          <span>{ev.text}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </section>
      </main>
    </div>
  );
}
```


---

## 5. Test checklist

Run everything with `DRY_RUN=true` + `simulate_call.py` first (no minutes used). Then repeat the items marked 📞 on real phones.

**Setup**
- [ ] `.env` is not in `git status`; the repo on GitHub has no keys
- [ ] `/api/health` shows the right `dry_run`, `db`, `llm`, `public_url`
- [ ] 📞 `hello_call.py` rings each of the 3 verified phones

**Scheduling**
- [ ] A reminder 1 minute ahead fires within the minute (status → calling)
- [ ] A past time fires in about 5 s instead of failing
- [ ] Invalid phone (`98765`) shows an error message on the form
- [ ] Restart uvicorn with a pending reminder → `restored` line, and it still fires
- [ ] **Call now** button works
- [ ] **Reset demo** clears everything, and `/api/health` shows `jobs: 0`

**Planner**
- [ ] medicine → critical (3 attempts), appointment → high (3), other → normal (2)
- [ ] Wrong LLM key → still gets a priority with `[fallback …]`

**Listener (speech)**
- [ ] 📞 "yes, I took it" → confirmed
- [ ] 📞 "call me in 5 minutes" → rescheduled, calls again about 5 min later
- [ ] "call me later" (no number) → rescheduled in 10 min
- [ ] 📞 "I fell and my knee hurts" → needs attention (red), Reporter starts "ATTENTION:"
- [ ] Silence twice → "try again later", retrying
- [ ] Gibberish twice → retrying
- [ ] Wrong LLM key → keyword fallback gives the same results, and the call never goes silent

**Keypad**
- [ ] 📞 1 → confirmed · 2 → rescheduled 10 min · 3 → needs attention

**Questions + Guardian**
- [ ] 📞 Question answered in the note → real answer + GUARDIAN PASSED, then asked again
- [ ] 📞 Question not in the note → "I don't have that information, I'll let the sender know."
- [ ] Medical-advice question ("can I take two?") → no advice is given
- [ ] A 3rd question in one call → ends the call and retries (max 2 answered)

**Retry / unreachable**
- [ ] 📞 Decline every call → retrying → unreachable after the plan's attempts
- [ ] Hang up in the middle of the message → counts as a failed attempt and retries
- [ ] Unverified number → SYSTEM error line in the transcript, then unreachable (no crash)

**Reporter + dashboard**
- [ ] Every ended call gets a one-sentence summary (≤ 25 words)
- [ ] Every transcript line has an agent tag; the colors match the status
- [ ] The dashboard updates within 2 s without a page refresh, on laptop and phone
- [ ] The v0 page works from the Vercel URL with the ngrok backend URL

**Languages**
- [ ] 📞 Hindi call speaks Hindi and understands हाँ / बाद में
- [ ] 📞 Kannada call speaks Kannada and understands ಹೌದು / ಆಮೇಲೆ
- [ ] A language removed from `ENABLED_LANGUAGES` → that call speaks English

**Network**
- [ ] Switch the laptop to the phone hotspot, restart ngrok, update URLs → still works

---

## 6. Common errors and fixes

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then activate again |
| 2 | `py` or `python` not found, or the wrong version | PATH | Use `py -3.11 -m venv .venv`; always activate the venv first |
| 3 | `ModuleNotFoundError` | venv not active or wrong folder | `cd backend`, `.\.venv\Scripts\Activate.ps1`, `pip install -r requirements.txt` |
| 4 | Twilio error **21219** "The number is unverified" | Trial accounts call verified numbers only | Console → Verified Caller IDs → add the number |
| 5 | Twilio error **21215** / "not authorized to call +91…" | India not enabled | Console → Voice → Settings → Geo permissions → India ✓ |
| 6 | Phone rings, trial message plays, then the call ends | Nobody pressed a key | On trial calls, press any key after the trial message |
| 7 | Call says "an application error has occurred" | Twilio could not reach the backend (Debugger shows **11200**) | ngrok running? `PUBLIC_URL` current? uvicorn running? Check 127.0.0.1:4040 for the failing request |
| 8 | Debugger **12100** document parse failure | Backend crashed and returned an HTML error | Read the uvicorn traceback; the dashboard SYSTEM line also helps |
| 9 | `RuntimeError: PUBLIC_URL is not set` in the transcript | `.env` empty and ngrok not running | Start ngrok, paste the https URL, restart uvicorn |
| 10 | Hindi or Kannada voice silent, or Debugger voice/language warning | That voice name is not available | Change `VOICE_HI` / `VOICE_KN` in `.env` (try `Polly.Aditi` for Hindi); otherwise drop the language from `ENABLED_LANGUAGES` |
| 11 | Speech not understood / always UNCLEAR | Noise, accent, speaking during the prompt | Speak after the prompt ends, close to the phone; use keypad 1/2/3 (that is what it is for) |
| 12 | Long pause after speaking | LLM slow | Normal max is about 2.5 s (`LLM_TIMEOUT`), then the keyword fallback takes over. Try `GROQ_MODEL=llama-3.1-8b-instant` |
| 13 | Groq **429** rate limit or **401** | Too many calls, or a bad key | Fallbacks keep the call working; wait a minute, or check the key |
| 14 | `[db] Supabase not working (...)` then SQLite | Wrong URL or key, or tables missing | Re-run `schema.sql`; use the **service_role/secret** key, not the anon key. "row-level security" error = anon key |
| 15 | Browser console: **CORS** error | Origin not allowed | `CORS_ORIGINS=*` (default) or add your exact Vercel URL; restart uvicorn |
| 16 | v0 page shows "backend offline" | Wrong or old ngrok URL, or an ngrok warning page | Paste the current https ngrok URL (no trailing `/`). The page already sends `ngrok-skip-browser-warning` |
| 17 | Vercel page cannot reach `http://localhost:8000` | Browsers block https → http calls | Use the ngrok https URL in the v0 page, or the fallback page at localhost |
| 18 | ngrok **ERR_NGROK_108** (one session only) | ngrok already running elsewhere | Close the other ngrok; only the backend laptop runs it |
| 19 | ngrok **ERR_NGROK_3200** endpoint offline | ngrok restarted, the address changed | Update `PUBLIC_URL` and the v0 Backend URL |
| 20 | `[Errno 10048]` port 8000 in use | Old uvicorn still running | `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F` |
| 21 | Call never fires | Time in the past on another day, server stopped, or after a Reset | `/api/health` → `jobs` should be ≥ 1; check the laptop clock and the time zone of the date picker |
| 22 | The same reminder calls twice | Two uvicorn processes (e.g. two laptops with the same DB) | Run the backend on **one** laptop only |
| 23 | Garbled Hindi or Kannada in PowerShell | Console encoding | `$env:PYTHONUTF8=1` before running scripts |
| 24 | Code change kills a live call | `--reload` restarted the server | Don't use `--reload` during demos; pending calls are restored anyway |
| 25 | Reporter summary empty for a few seconds | It runs after the call ends | Wait 2–5 s; a template summary is used if the LLM fails |

Twilio's own error page: Console → Monitor → Logs → Errors (the "Debugger"). Every error code links to an explanation.

---

## 7. Demo script (3 minutes)

**Before the judges arrive** (5 min): `.env` has `DRY_RUN=false` and `DEMO_MODE=true`. ngrok is running, `PUBLIC_URL` and the v0 Backend URL are updated, uvicorn is running without `--reload`. Click **Reset demo**. Pushkar's phone (**Phone A**) is on speaker, volume max. Satyam's phone (**Phone B**) is ready to decline calls. The dashboard is on the big screen at 125% zoom and the backup video is open in another tab.

> **One order change from the brief:** "call me in 5 minutes" ends the call, so on Phone A the **question comes first** and the reschedule second. The "ignore" part runs on Phone B at the same time, so it finishes inside 3 minutes.

| Time | Who | Say / do | Screen shows |
|---|---|---|---|
| 0:00–0:20 | Sritej | "Medicine times and check-ins get missed because elders can't rely on texts or apps, and families never know if a message was heard. CareCall phones them, speaks the message in their language, listens, acts, and reports back." | Dashboard (empty) |
| 0:20–0:40 | Judge (Sritej guides) | Judge types Phone A's number, message **"Please take your blood pressure tablet after breakfast."**, note **"The blue tablet. The box is on the kitchen shelf. Take it with water."**, time = default (next minute), English → **Schedule** | Card appears: **CRITICAL** + PLANNER line: "3 attempts" |
| 0:40–0:50 | Satyam | Schedules Phone B: **"Please call Aunt Meena to wish her happy birthday."**, then clicks **Call now** | Card: **NORMAL**, 2 attempts |
| 0:50–1:20 | Sritej | While waiting: "Four agents, each a separate LLM call with its own job. The Planner just set priority and the retry plan. The Listener runs live on the call, the Guardian checks every answer, and the Reporter writes the summary. Every line here is tagged by the agent that acted." Phone B rings → **Satyam declines** (and again 20 s later) | Phone B: calling → retrying → … |
| 1:20–1:35 | Pushkar | Phone A rings → answer on speaker → press a key for the trial message | Status **calling** |
| 1:35–1:55 | Pushkar | The message plays. Pushkar asks: **"Can I take it with tea?"** | PERSON → LISTENER QUESTION → **GUARDIAN** line → "I don't have that information, I'll let the sender know." |
| 1:55–2:15 | Pushkar | When asked again: **"Call me in 5 minutes."** → AI: "Okay, I will call you again in 5 minutes." | Status **rescheduled**, then the **Reporter** sentence |
| 2:15–2:35 | Sritej | Points to Phone B: "Nobody picked up. It retried on the Planner's plan, then stopped. No spam, no escalation, and the sender knows exactly what happened." | Phone B: **unreachable** + Reporter sentence |
| 2:35–2:50 | Sritej | Opens a transcript: "Speech or keypad, so it works even if speech recognition fails. If the AI is down, a keyword fallback keeps the call going." | Tagged transcript |
| 2:50–3:00 | Sritej | **"No app for the elder, and the sender always knows what happened."** | — |

**If something breaks:** if speech fails, Pushkar presses **2** (later). If no call arrives within 30 s, click **Call now**. If Twilio or the network is down, play the backup video immediately and narrate over it.

---

## 8. Cut list (in priority order)

Cut from the top when you are behind:

1. **Hindi and Kannada.** Set `ENABLED_LANGUAGES=en`; the code falls back to English automatically.
2. **Guardian agent.** In `main.py`'s QUESTION branch, speak `res["reply"]` directly. The Listener prompt already says to answer only from the note.
3. **Dashboard polish.** Use the plain fallback page at http://localhost:8000 (it already has colors, tags and the summary).
4. **v0 page.** Use the fallback page (decision at hour 6).
5. **Supabase.** `DB_BACKEND=sqlite` (decision at hour 6).
6. **Reporter LLM.** The template summaries already exist as the fallback.

**Never cut:** the real call, listening (speech + keypad), reschedule, and the Listener agent with its keyword fallback.
