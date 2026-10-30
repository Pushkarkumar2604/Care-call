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
