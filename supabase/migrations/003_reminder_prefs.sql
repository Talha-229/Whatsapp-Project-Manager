-- Per-user default and optional per-meeting reminder lead time (minutes before event)
alter table public.users
  add column if not exists meeting_reminder_lead_minutes integer not null default 15;

alter table public.meetings
  add column if not exists reminder_lead_minutes integer;

comment on column public.users.meeting_reminder_lead_minutes is 'Default minutes before meeting to send WhatsApp reminder';
comment on column public.meetings.reminder_lead_minutes is 'Override lead minutes for this row; null = use creator default at insert time';
