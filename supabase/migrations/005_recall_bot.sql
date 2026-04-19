-- Recall.ai notetaker bot id (optional)
alter table public.meetings
  add column if not exists recall_bot_id text;

create index if not exists idx_meetings_recall_bot on public.meetings (recall_bot_id);

comment on column public.meetings.recall_bot_id is 'Recall.ai bot id when a notetaker was scheduled for this meeting';
