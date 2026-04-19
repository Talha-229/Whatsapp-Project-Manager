-- Personal tasks created via WhatsApp (assignee remains for legacy rows)
alter table public.tasks
  add column if not exists created_by_wa_id text,
  add column if not exists notes text;

create index if not exists idx_tasks_created_by_wa on public.tasks (created_by_wa_id);
create index if not exists idx_tasks_wa_due on public.tasks (created_by_wa_id, due_date);

comment on column public.tasks.created_by_wa_id is 'WhatsApp id (digits) of user who owns this task; null for seeded/legacy tasks';
