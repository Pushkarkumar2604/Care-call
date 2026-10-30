-- ============================================================================
-- CareCall: General-Purpose AI Check-in Call Service Schema
-- ============================================================================

-- 1) Contacts: People who receive check-in calls
create table if not exists contacts (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid, -- links to caregiver auth user if signed in
  name text not null,
  phone text not null,
  secret_check text not null default '1985', -- second factor (year of birth or secret word)
  language text not null default 'en-IN',
  reminders text default '', -- daily tasks / reminders
  notes text default '', -- facts known to caregiver
  caregiver_contact text default '', -- phone / email for notifications
  created_at timestamptz not null default now()
);

-- 2) Call Attempts: Tracks retries and Twilio call status
create table if not exists call_attempts (
  id bigserial primary key,
  contact_id uuid not null references contacts(id) on delete cascade,
  attempt_no int not null default 1,
  twilio_sid text not null default '',
  status text not null default 'initiated', -- initiated, ringing, answered, completed, busy, no-answer, failed
  started_at timestamptz not null default now()
);

-- 3) Call Sessions: Live conversational state machine for twilio-respond
-- States: asking_identity -> verifying -> verified | not_contact | failed
create table if not exists call_sessions (
  call_sid text primary key,
  contact_id uuid not null references contacts(id) on delete cascade,
  state text not null default 'asking_identity',
  unclear_count int not null default 0,
  history jsonb not null default '[]'::jsonb,
  updated_at timestamptz not null default now()
);

-- 4) Call Results: Structured check-in analysis after call completion
create table if not exists call_results (
  id bigserial primary key,
  contact_id uuid not null references contacts(id) on delete cascade,
  mood int default 5,
  ate boolean default true,
  did_reminders boolean default true,
  sleep text default 'good',
  discomfort boolean default false,
  emergency boolean default false,
  concerns jsonb not null default '[]'::jsonb,
  summary text default '',
  tone_note text default '',
  severity text not null default 'none', -- high, medium, low, none
  flags jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

-- 5) Alerts: Caregiver notifications for worrying signs, identity failures, unreachable
create table if not exists alerts (
  id bigserial primary key,
  contact_id uuid not null references contacts(id) on delete cascade,
  severity text not null default 'medium', -- high, medium, low
  reasons jsonb not null default '[]'::jsonb,
  resolved boolean not null default false,
  created_at timestamptz not null default now()
);

-- Indexes for performance
create index if not exists call_attempts_contact_idx on call_attempts(contact_id);
create index if not exists call_sessions_contact_idx on call_sessions(contact_id);
create index if not exists call_results_contact_idx on call_results(contact_id);
create index if not exists alerts_contact_idx on alerts(contact_id);

-- Enable RLS
alter table contacts enable row level security;
alter table call_attempts enable row level security;
alter table call_sessions enable row level security;
alter table call_results enable row level security;
alter table alerts enable row level security;

-- Service role bypass policies (backend & edge functions use service role key)
create policy if not exists "Service role full access on contacts" on contacts for all using (true) with check (true);
create policy if not exists "Service role full access on call_attempts" on call_attempts for all using (true) with check (true);
create policy if not exists "Service role full access on call_sessions" on call_sessions for all using (true) with check (true);
create policy if not exists "Service role full access on call_results" on call_results for all using (true) with check (true);
create policy if not exists "Service role full access on alerts" on alerts for all using (true) with check (true);
