-- Phase 1 schema + seed (run in Supabase SQL editor or supabase db push)

create extension if not exists "pgcrypto";

-- Users
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  whatsapp_number text not null unique,
  email text,
  role text default 'employee',
  google_refresh_token_encrypted text,
  google_connected_at timestamptz,
  created_at timestamptz default now()
);

-- Projects
create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  owner text not null,
  status text not null default 'active',
  deadline date,
  created_at timestamptz default now()
);

-- Tasks
create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  project_id uuid references public.projects(id) on delete set null,
  title text not null,
  assignee text not null,
  due_date date,
  status text not null default 'open',
  created_at timestamptz default now()
);

-- Meetings
create table if not exists public.meetings (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  attendees text[] default '{}',
  scheduled_at timestamptz not null,
  meeting_url text,
  transcript text,
  summary text,
  reminder_sent_at timestamptz,
  created_by_wa_id text,
  created_at timestamptz default now()
);

create index if not exists idx_meetings_scheduled on public.meetings (scheduled_at);
create index if not exists idx_meetings_reminder on public.meetings (reminder_sent_at);

-- Action items
create table if not exists public.action_items (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid references public.meetings(id) on delete cascade,
  owner text,
  description text not null,
  due_date date,
  resolved boolean default false,
  created_at timestamptz default now()
);

-- Policies
create table if not exists public.policies (
  id uuid primary key default gen_random_uuid(),
  category text not null,
  title text not null,
  content text not null,
  last_updated date default current_date
);

-- RLS: enable later with policies; backend uses service_role which bypasses RLS in Supabase.

-- Seed: 3 projects, 10 tasks, 2 past meetings with transcripts, 5 policies, users
insert into public.users (name, whatsapp_number, email, role) values
  ('Rania Ali', '15551234567', 'rania@example.com', 'manager'),
  ('Omar Khan', '15559876543', 'omar@example.com', 'engineer'),
  ('Demo User', '15550001111', 'demo@example.com', 'employee')
on conflict (whatsapp_number) do nothing;

insert into public.projects (name, owner, status, deadline) values
  ('Mobile App Launch', 'Rania Ali', 'active', '2026-06-01'),
  ('Data Migration', 'Omar Khan', 'active', '2026-05-15'),
  ('HR Portal', 'Rania Ali', 'on_hold', '2026-08-20')
on conflict do nothing;

-- Map project names to ids for tasks (use subqueries)
insert into public.tasks (project_id, title, assignee, due_date, status)
select p.id, v.title, v.assignee, v.due_date::date, v.status
from (values
  ('Mobile App Launch', 'QA sign-off', 'Omar Khan', '2026-04-25', 'open'),
  ('Mobile App Launch', 'App store listing', 'Rania Ali', '2026-04-28', 'open'),
  ('Data Migration', 'Validate ETL', 'Omar Khan', '2026-04-20', 'done'),
  ('Data Migration', 'Cutover window', 'Rania Ali', '2026-05-10', 'open'),
  ('HR Portal', 'SSO integration', 'Omar Khan', '2026-04-18', 'open'),
  ('Mobile App Launch', 'Crash analytics', 'Omar Khan', '2026-04-22', 'open'),
  ('Data Migration', 'Rollback plan', 'Rania Ali', '2026-05-01', 'open'),
  ('HR Portal', 'Policy review', 'Rania Ali', '2026-04-30', 'open'),
  ('HR Portal', 'Employee FAQ', 'Omar Khan', '2026-05-05', 'open'),
  ('Mobile App Launch', 'Beta feedback triage', 'Rania Ali', '2026-04-26', 'open')
) as v(pname, title, assignee, due_date, status)
join public.projects p on p.name = v.pname;

insert into public.meetings (title, attendees, scheduled_at, meeting_url, transcript, summary, created_by_wa_id)
values
  (
    'Q1 Planning',
    array['rania@example.com','omar@example.com'],
    '2026-03-10T14:00:00Z',
    'https://meet.google.com/abc-defg-hij',
    'Rania: We will prioritize mobile launch. Omar: ETL completes by May.',
    'Prioritize mobile launch; ETL target May.',
    '15550001111'
  ),
  (
    'Retro',
    array['omar@example.com'],
    '2026-03-20T16:00:00Z',
    'https://meet.google.com/zzz-yyyy-xxx',
    'Discussed delays in QA. Action: add regression suite.',
    'QA delays noted; add regression suite.',
    '15550001111'
  );

insert into public.action_items (meeting_id, owner, description, due_date, resolved)
select m.id, 'Omar Khan', 'Add regression suite', '2026-04-01', false
from public.meetings m where m.title = 'Retro' limit 1;

insert into public.policies (category, title, content, last_updated) values
  ('Workplace', 'Remote work policy', 'Employees may work remotely up to 3 days per week with manager approval. Core hours 10:00–15:00 local time apply on in-office days.', '2026-01-10'),
  ('Leave', 'Annual leave', 'Annual leave accrues at 1.25 days per month. Requests require 2 weeks notice for blocks over 3 days.', '2026-01-10'),
  ('Security', 'Device policy', 'Company devices must use full-disk encryption and MDM enrollment. Personal devices may not access customer data.', '2026-02-01'),
  ('Conduct', 'Code of conduct', 'Harassment-free workplace; escalate concerns to HR or the ethics hotline.', '2025-12-01'),
  ('Expense', 'Travel expenses', 'Pre-approval required for international travel. Receipts within 14 days.', '2026-03-01');
