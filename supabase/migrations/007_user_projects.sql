-- Projects created via WhatsApp (seed rows keep created_by_wa_id null)
alter table public.projects
  add column if not exists created_by_wa_id text;

create index if not exists idx_projects_created_by_wa on public.projects (created_by_wa_id);

comment on column public.projects.created_by_wa_id is 'WhatsApp id (digits) of user who owns this project; null for legacy/seed';
