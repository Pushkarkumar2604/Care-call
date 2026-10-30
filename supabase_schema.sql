-- CareCall database schema

-- 1) People who receive calls
create table if not exists contacts (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  phone text not null,
  language text not null default 'en-IN',
  family_name text,
  family_phone text,
  created_at timestamptz not null default now()
);

-- 2) Scheduled reminder calls
create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid not null references contacts(id) on delete cascade,
  message text not null,
  scheduled_at timestamptz not null,
  status text not null default 'scheduled',
  attempts int not null default 0,
  max_attempts int not null default 3,
  created_at timestamptz not null default now()
);

-- 3) Every call attempt and what happened in it
create table if