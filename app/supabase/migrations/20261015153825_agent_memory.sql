-- Additive memory v1. Business ICP rules and existing profile snapshots stay unchanged.
alter table atp.jobs add column queue text not null default 'live';
alter table atp.runs add column memory_snapshot jsonb not null default '{}';

create table atp.memory_contexts (
 id uuid primary key default gen_random_uuid(), org_id uuid not null, owner_id uuid not null references auth.users(id),
 kind text not null check(kind in ('company','project','general')), name text not null check(length(name) between 1 and 200),
 description text not null default '', external_id text not null default '',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 expires_at timestamptz not null, deleted_at timestamptz
);
create table atp.memory_shares (
 context_id uuid not null references atp.memory_contexts(id) on delete cascade,
 user_id uuid not null references auth.users(id) on delete cascade,
 permission text not null check(permission in ('read','edit')), primary key(context_id,user_id)
);
create table atp.memory_entries (
 id uuid primary key default gen_random_uuid(), context_id uuid not null references atp.memory_contexts(id) on delete cascade,
 org_id uuid not null, kind text not null check(kind in ('fact','decision','goal','question','learning')),
 text text not null check(length(text) between 1 and 2000), source text not null,
 information_as_of text not null default '', status text not null check(status in ('draft','confirmed','superseded')),
 version integer not null default 1 check(version>0), applies_to jsonb not null default '[]',
 created_by uuid not null references auth.users(id), confirmed_by uuid references auth.users(id), confirmed_at timestamptz,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 expires_at timestamptz not null, deleted_at timestamptz
);
create table atp.memory_entry_versions (
 entry_id uuid not null references atp.memory_entries(id) on delete cascade, version integer not null,
 context_id uuid not null references atp.memory_contexts(id) on delete cascade, data jsonb not null,
 created_at timestamptz not null default now(), primary key(entry_id,version)
);
create table atp.memory_tasks (
 id uuid primary key default gen_random_uuid(), org_id uuid not null, created_by uuid not null references auth.users(id),
 context_id uuid not null references atp.memory_contexts(id), kind text not null check(kind in ('project','learning')),
 input jsonb not null, memory_snapshot jsonb not null, result jsonb, checkpoint jsonb not null default '{}',
 status text not null default 'queued' check(status in ('queued','running','completed','failed','cancelled')),
 queue text not null default 'live', idempotency_key text not null, payload_hash text not null,
 lease_token uuid, lease_until timestamptz, heartbeat_at timestamptz, attempt integer not null default 0,
 usage jsonb not null default '{}', usage_saved_at timestamptz not null default now(),
 pdf_path text, error_code text, created_at timestamptz not null default now(), expires_at timestamptz not null,
 unique(created_by,idempotency_key)
);
create table atp.memory_links (
 id uuid primary key default gen_random_uuid(), context_id uuid not null references atp.memory_contexts(id),
 company_run_id uuid references atp.runs(id) on delete cascade,
 task_id uuid references atp.memory_tasks(id) on delete cascade,
 check ((company_run_id is null) <> (task_id is null)),
 unique(context_id,company_run_id), unique(context_id,task_id)
);
create table atp.memory_feedback (
 id uuid primary key default gen_random_uuid(), org_id uuid not null, context_id uuid not null references atp.memory_contexts(id) on delete cascade,
 created_by uuid not null references auth.users(id), kind text not null check(kind in ('correction','helpful','unhelpful','outcome')),
 text text not null check(length(text) between 1 and 3000), reference text not null default '',
 briefing_id uuid references atp.briefings(id) on delete cascade, task_id uuid references atp.memory_tasks(id) on delete cascade,
 created_at timestamptz not null default now(), expires_at timestamptz not null
);
create table atp.memory_imports (
 context_id uuid not null references atp.memory_contexts(id) on delete cascade,
 source_id uuid not null, source_version integer not null, entry_id uuid not null references atp.memory_entries(id) on delete cascade,
 primary key(context_id,source_id,source_version)
);
create index memory_entries_context on atp.memory_entries(context_id,status,updated_at desc);
create index memory_tasks_queue on atp.memory_tasks(queue,status,created_at);
create index memory_links_run on atp.memory_links(company_run_id);
create index memory_links_task on atp.memory_links(task_id);

create function atp_private.context_allowed(cid uuid, editing boolean default false) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from atp.memory_contexts c
 where c.id=cid and c.deleted_at is null and c.expires_at>now()
 and atp_private.member_of(c.org_id) and (c.owner_id=auth.uid() or exists(
 select 1 from atp.memory_shares s where s.context_id=c.id and s.user_id=auth.uid()
 and (not editing or s.permission='edit'))))
$$;
create function atp_private.context_owned(cid uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from atp.memory_contexts c where c.id=cid and c.owner_id=auth.uid() and atp_private.member_of(c.org_id) and c.deleted_at is null)
$$;
create function atp_private.context_share_target(cid uuid, uid uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select atp_private.context_owned(cid) and exists(select 1 from atp.memory_contexts c join atp.memberships m on m.org_id=c.org_id where c.id=cid and m.user_id=uid and m.active)
$$;
create function atp_private.meeting_memory_allowed(mid uuid) returns boolean
language sql stable security definer set search_path='' as $$
 select not exists(select 1 from atp.memory_links l join atp.runs r on r.id=l.company_run_id
 where r.meeting_id=mid and not atp_private.context_allowed(l.context_id))
$$;
create function atp_private.task_allowed(tid uuid, editing boolean default false) returns boolean
language sql stable security definer set search_path='' as $$
 select exists(select 1 from atp.memory_tasks t where t.id=tid and atp_private.member_of(t.org_id)
 and atp_private.context_allowed(t.context_id,editing) and t.expires_at>now()
 and not exists(select 1 from atp.memory_links l where l.task_id=t.id and not atp_private.context_allowed(l.context_id)))
$$;
-- A shared meeting never widens access to its memory contexts, including raw runs/facts.
create policy memory_meeting_guard on atp.meetings as restrictive for select to authenticated using(atp_private.meeting_memory_allowed(id));
create policy memory_run_guard on atp.runs as restrictive for select to authenticated using(atp_private.meeting_memory_allowed(meeting_id));
create policy memory_brief_guard on atp.briefings as restrictive for select to authenticated using(atp_private.meeting_memory_allowed(meeting_id));
create policy memory_fact_guard on atp.facts as restrictive for select to authenticated using(exists(select 1 from atp.runs r where r.id=run_id));
create policy memory_source_guard on atp.sources as restrictive for select to authenticated using(exists(select 1 from atp.runs r where r.id=run_id));

alter table atp.memory_contexts enable row level security;
alter table atp.memory_shares enable row level security;
alter table atp.memory_entries enable row level security;
alter table atp.memory_entry_versions enable row level security;
alter table atp.memory_tasks enable row level security;
alter table atp.memory_links enable row level security;
alter table atp.memory_feedback enable row level security;
alter table atp.memory_imports enable row level security;
create policy context_read on atp.memory_contexts for select to authenticated using(atp_private.context_allowed(id));
create policy context_add on atp.memory_contexts for insert to authenticated with check(owner_id=auth.uid() and atp_private.member_of(org_id));
create policy context_update on atp.memory_contexts for update to authenticated using(atp_private.context_owned(id)) with check(owner_id=auth.uid() and atp_private.member_of(org_id));
create policy share_read on atp.memory_shares for select to authenticated using(atp_private.context_allowed(context_id));
create policy share_write on atp.memory_shares for all to authenticated using(atp_private.context_owned(context_id)) with check(atp_private.context_share_target(context_id,user_id));
create policy entry_read on atp.memory_entries for select to authenticated using(atp_private.context_allowed(context_id) and deleted_at is null and expires_at>now());
create policy entry_add on atp.memory_entries for insert to authenticated with check(atp_private.context_allowed(context_id,true) and created_by=auth.uid() and exists(select 1 from atp.memory_contexts c where c.id=context_id and c.org_id=memory_entries.org_id));
create policy entry_update on atp.memory_entries for update to authenticated using(atp_private.context_allowed(context_id,true)) with check(atp_private.context_allowed(context_id,true));
create policy version_read on atp.memory_entry_versions for select to authenticated using(atp_private.context_allowed(context_id));
create policy version_add on atp.memory_entry_versions for insert to authenticated with check(atp_private.context_allowed(context_id,true) and exists(select 1 from atp.memory_entries e where e.id=entry_id and e.context_id=memory_entry_versions.context_id));
create policy task_read on atp.memory_tasks for select to authenticated using(atp_private.task_allowed(id));
create policy task_add on atp.memory_tasks for insert to authenticated with check(created_by=auth.uid() and atp_private.member_of(org_id) and atp_private.context_allowed(context_id,true));
create policy feedback_read on atp.memory_feedback for select to authenticated using(atp_private.context_allowed(context_id) and (briefing_id is null or exists(select 1 from atp.briefings b where b.id=briefing_id)) and (task_id is null or atp_private.task_allowed(task_id)));
create policy feedback_add on atp.memory_feedback for insert to authenticated with check(created_by=auth.uid() and atp_private.member_of(org_id) and atp_private.context_allowed(context_id,true) and (briefing_id is null or exists(select 1 from atp.briefings b where b.id=briefing_id)) and (task_id is null or atp_private.task_allowed(task_id)));
create policy link_read on atp.memory_links for select to authenticated using(atp_private.context_allowed(context_id));
create policy link_add on atp.memory_links for insert to authenticated with check(atp_private.context_allowed(context_id) and ((company_run_id is not null and exists(select 1 from atp.runs r where r.id=company_run_id and r.created_by=auth.uid())) or (task_id is not null and exists(select 1 from atp.memory_tasks t where t.id=task_id and t.created_by=auth.uid()))));
create policy import_read on atp.memory_imports for select to authenticated using(atp_private.context_allowed(context_id));
create policy import_add on atp.memory_imports for insert to authenticated with check(atp_private.context_allowed(context_id,true));

-- Ownership, tenant, context and original retention deadlines cannot be moved by edits.
create function atp_private.memory_immutable_identity() returns trigger language plpgsql set search_path='' as $$
begin
 if new.org_id<>old.org_id or new.created_at<>old.created_at or new.expires_at<>old.expires_at then raise exception 'immutable memory identity'; end if;
 if tg_table_name='memory_contexts' then
   if new.owner_id<>old.owner_id then raise exception 'immutable owner'; end if;
 else
   if new.context_id<>old.context_id or new.created_by<>old.created_by then raise exception 'immutable context'; end if;
 end if;
 return new;
end $$;
create trigger memory_context_identity before update on atp.memory_contexts for each row execute function atp_private.memory_immutable_identity();
create trigger memory_entry_identity before update on atp.memory_entries for each row execute function atp_private.memory_immutable_identity();

revoke all on all tables in schema atp from anon;
grant select,insert,update on atp.memory_contexts,atp.memory_entries to authenticated;
grant select,insert,delete on atp.memory_shares to authenticated;
grant select,insert on atp.memory_entry_versions,atp.memory_feedback,atp.memory_tasks,atp.memory_links,atp.memory_imports to authenticated;
grant all on atp.memory_contexts,atp.memory_shares,atp.memory_entries,atp.memory_entry_versions,atp.memory_tasks,atp.memory_links,atp.memory_feedback,atp.memory_imports to atp_worker;
revoke all on function atp_private.context_allowed(uuid,boolean),atp_private.context_owned(uuid),atp_private.meeting_memory_allowed(uuid),atp_private.task_allowed(uuid,boolean),atp_private.memory_immutable_identity() from public,anon;
grant execute on function atp_private.context_allowed(uuid,boolean),atp_private.context_owned(uuid),atp_private.meeting_memory_allowed(uuid),atp_private.task_allowed(uuid,boolean) to authenticated,atp_worker;
revoke all on function atp_private.context_share_target(uuid,uuid) from public,anon;
grant execute on function atp_private.context_share_target(uuid,uuid) to authenticated,atp_worker;
