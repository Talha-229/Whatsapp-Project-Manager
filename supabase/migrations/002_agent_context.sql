-- Per-user agent memory (scheduling drafts, etc.)
alter table public.users
  add column if not exists agent_context jsonb not null default '{}'::jsonb;

comment on column public.users.agent_context is 'JSON: e.g. {"draft": {"meeting_title", "start_iso", "attendee_emails", ...}}';
