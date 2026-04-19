-- Deduplicate WhatsApp reminders for Google Calendar primary-calendar events
create table if not exists public.calendar_reminder_sent (
  id uuid primary key default gen_random_uuid(),
  whatsapp_number text not null,
  google_event_id text not null,
  event_start_utc timestamptz not null,
  sent_at timestamptz not null default now(),
  unique (whatsapp_number, google_event_id, event_start_utc)
);

create index if not exists idx_calendar_reminder_sent_wa on public.calendar_reminder_sent (whatsapp_number);

-- Bot-created meetings store Google event id so we do not ping twice (meetings row + calendar poll)
alter table public.meetings
  add column if not exists google_calendar_event_id text;

create index if not exists idx_meetings_gcal_lookup on public.meetings (created_by_wa_id, google_calendar_event_id);
