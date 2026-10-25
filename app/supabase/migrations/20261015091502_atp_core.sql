-- Dedicated ATP application schema. It is deliberately not exposed through PostgREST.
-- The web login MUST SET LOCAL ROLE authenticated and request.jwt.claims per transaction.
create schema if not exists atp;
create schema if not exists atp_private;
revoke all on schema atp, atp_private from public, anon;
grant usage on schema atp, atp_private to authenticated;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'atp_web') then
    create role atp_web nologin noinherit nobypassrls;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'atp_worker') then
    create role atp_worker nologin noinherit bypassrls;
  end if;
end $$;
grant authenticated to atp_web;
grant usage on schema atp, atp_private to atp_worker;

create table atp.memberships (
  user_id uuid not null references auth.users(id) on delete cascade,
  org_id uuid not null,
  role text not null check (role in ('member', 'admin')) default 'member',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (user_id, org_id)
);
create table atp.meetings (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  owner_id uuid not null references auth.users(id),
  input jsonb not null,
  company_name text not null,
  scheduled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz
);
create table atp.shares (
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  permission text not null check (permission in ('read', 'edit')),
  granted_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  primary key(meeting_id, user_id)
);
create table atp.participants (
  id uuid not null,
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  data jsonb not null,
  primary key(meeting_id, id)
);
create table atp.profile_versions (
  org_id uuid not null,
  version text not null,
  content_hash text not null,
  content jsonb not null,
  active boolean not null default false,
  activated_by uuid references auth.users(id),
  created_at timestamptz not null default now(),
  primary key(org_id, version)
);
create unique index profile_one_active on atp.profile_versions(org_id) where active;
create table atp.runs (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  org_id uuid not null,
  created_by uuid not null references auth.users(id),
  result_version int not null check(result_version > 0),
  input_snapshot jsonb not null,
  clarified_input jsonb not null,
  profile jsonb not null,
  profile_version text not null,
  profile_hash text not null,
  status text not null default 'queued',
  phase text not null default 'validating',
  version int not null default 1,
  clarifications jsonb not null default '[]',
  answers jsonb not null default '{}',
  usage jsonb not null default '{}',
  briefing_id uuid,
  pdf_status text not null default 'pending',
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(meeting_id, result_version),
  check (status in ('queued','validating','needs_clarification','researching','evaluating','generating','completed','partial','failed','cancelled'))
);
create table atp.jobs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null unique references atp.runs(id) on delete cascade,
  org_id uuid not null,
  status text not null default 'queued' check (status in ('queued','running','paused','completed','failed','cancelled')),
  attempt int not null default 0,
  worker_id text,
  lease_token uuid,
  lease_until timestamptz,
  heartbeat_at timestamptz,
  usage_saved_at timestamptz,
  checkpoint jsonb not null default '{}',
  usage jsonb not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index job_claim on atp.jobs(status, lease_until, created_at);
create table atp.briefings (
  id uuid primary key,
  run_id uuid not null unique references atp.runs(id) on delete cascade,
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  org_id uuid not null,
  result_version int not null,
  result jsonb not null,
  pdf_path text,
  pdf_error text,
  pdf_status text not null,
  created_at timestamptz not null default now(),
  unique(meeting_id, result_version)
);
create table atp.sources (
  run_id uuid not null references atp.runs(id) on delete cascade,
  id uuid not null,
  data jsonb not null,
  primary key(run_id,id)
);
create table atp.facts (
  run_id uuid not null references atp.runs(id) on delete cascade,
  id uuid not null,
  data jsonb not null,
  primary key(run_id,id)
);
create table atp.decisions (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  run_id uuid not null references atp.runs(id) on delete cascade,
  decided_by uuid not null references auth.users(id),
  data jsonb not null,
  created_at timestamptz not null default now()
);
create table atp.drafts (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id),
  org_id uuid not null,
  preview jsonb not null,
  warnings jsonb not null,
  usage jsonb not null,
  created_at timestamptz not null default now()
);
create table atp.idempotency_keys (
  owner_id uuid not null references auth.users(id),
  key text not null,
  payload_hash text not null,
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  run_id uuid not null references atp.runs(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key(owner_id,key)
);
create table atp.deletion_jobs (
  meeting_id uuid primary key references atp.meetings(id) on delete cascade,
  org_id uuid not null,
  requested_by uuid references auth.users(id),
  status text not null default 'queued',
  attempt int not null default 0,
  lease_until timestamptz,
  created_at timestamptz not null default now()
);
-- Non-personal tombstone survives purge, so restores can reapply the deletion list.
create table atp.deletion_tombstones (
  meeting_id uuid primary key,
  org_id uuid not null,
  requested_at timestamptz not null default now(),
  purged_at timestamptz
);
create table atp.integration_events (
  source text not null, event_id text not null, booking_id text not null,
  revision int not null, occurred_at timestamptz not null, kind text not null,
  data jsonb not null, primary key(source,event_id)
);
create table atp.outbox (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references atp.meetings(id) on delete cascade,
  target_system text not null, target_record_id text not null, result_version int not null,
  approved_by uuid not null references auth.users(id),
  payload jsonb not null, status text not null default 'pending',
  unique(target_system,target_record_id,result_version)
);

-- ACL lookups require narrow, non-exposed definer functions to avoid recursive RLS.
-- They never accept caller-supplied identity or role; all identity comes from auth.uid().
create function atp_private.member_of(p_org uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null and exists (
    select 1 from atp.memberships where user_id=auth.uid() and org_id=p_org and active
  );
$$;
create function atp_private.meeting_allowed(p_id uuid, p_edit boolean default false) returns boolean
language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null and exists (
    select 1 from atp.meetings m
    where m.id=p_id and m.deleted_at is null and atp_private.member_of(m.org_id)
      and not exists(select 1 from atp.deletion_tombstones t where t.meeting_id=m.id)
      and (m.owner_id=auth.uid() or exists (
        select 1 from atp.shares s where s.meeting_id=m.id and s.user_id=auth.uid()
          and (not p_edit or s.permission='edit')))
  );
$$;
create function atp_private.meeting_owned(p_id uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null and exists (
    select 1 from atp.meetings m where m.id=p_id and m.owner_id=auth.uid()
      and m.deleted_at is null and atp_private.member_of(m.org_id)
  );
$$;
create function atp_private.session_active(p_session uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select auth.uid() is not null and exists (
    select 1 from auth.sessions s join auth.users u on u.id=s.user_id
    where s.id=p_session and s.user_id=auth.uid() and u.deleted_at is null
      and (u.banned_until is null or u.banned_until<now())
      and (s.not_after is null or s.not_after>now())
  );
$$;
revoke all on all functions in schema atp_private from public,anon;
grant execute on all functions in schema atp_private to authenticated,atp_worker;

alter table atp.memberships enable row level security;
create policy membership_self on atp.memberships for select to authenticated using(user_id=auth.uid());
alter table atp.meetings enable row level security;
create policy meeting_read on atp.meetings for select to authenticated using(atp_private.meeting_allowed(id));
create policy meeting_insert on atp.meetings for insert to authenticated with check(owner_id=auth.uid() and atp_private.member_of(org_id));
create policy meeting_update on atp.meetings for update to authenticated using(atp_private.meeting_allowed(id,true)) with check(atp_private.member_of(org_id));
alter table atp.shares enable row level security;
create policy share_read on atp.shares for select to authenticated using(atp_private.meeting_allowed(meeting_id));
create policy share_insert on atp.shares for insert to authenticated with check(atp_private.meeting_owned(meeting_id) and granted_by=auth.uid());
create policy share_delete on atp.shares for delete to authenticated using(atp_private.meeting_owned(meeting_id));
alter table atp.profile_versions enable row level security;
create policy profile_read on atp.profile_versions for select to authenticated using(atp_private.member_of(org_id));
create policy profile_insert on atp.profile_versions for insert to authenticated with check(atp_private.member_of(org_id) and not active);
alter table atp.participants enable row level security;
create policy participant_read on atp.participants for select to authenticated using(atp_private.meeting_allowed(meeting_id));
create policy participant_write on atp.participants for all to authenticated using(atp_private.meeting_allowed(meeting_id,true)) with check(atp_private.meeting_allowed(meeting_id,true));
alter table atp.runs enable row level security;
create policy run_read on atp.runs for select to authenticated using(atp_private.meeting_allowed(meeting_id));
create policy run_insert on atp.runs for insert to authenticated with check(atp_private.meeting_allowed(meeting_id,true) and created_by=auth.uid() and atp_private.member_of(org_id));
create policy run_update on atp.runs for update to authenticated using(atp_private.meeting_allowed(meeting_id,true)) with check(atp_private.meeting_allowed(meeting_id,true));
alter table atp.jobs enable row level security;
create policy job_read on atp.jobs for select to authenticated using(exists(select 1 from atp.runs r where r.id=run_id));
create policy job_insert on atp.jobs for insert to authenticated with check(exists(select 1 from atp.runs r where r.id=run_id and atp_private.meeting_allowed(r.meeting_id,true)));
create policy job_update on atp.jobs for update to authenticated using(exists(select 1 from atp.runs r where r.id=run_id and atp_private.meeting_allowed(r.meeting_id,true))) with check(exists(select 1 from atp.runs r where r.id=run_id and atp_private.meeting_allowed(r.meeting_id,true)));
alter table atp.briefings enable row level security;
create policy briefing_read on atp.briefings for select to authenticated using(atp_private.meeting_allowed(meeting_id));
create policy briefing_update on atp.briefings for update to authenticated using(atp_private.meeting_allowed(meeting_id,true)) with check(atp_private.meeting_allowed(meeting_id,true));
alter table atp.sources enable row level security;
create policy source_read on atp.sources for select to authenticated using(exists(select 1 from atp.runs r where r.id=run_id));
alter table atp.facts enable row level security;
create policy fact_read on atp.facts for select to authenticated using(exists(select 1 from atp.runs r where r.id=run_id));
alter table atp.decisions enable row level security;
create policy decision_read on atp.decisions for select to authenticated using(atp_private.meeting_allowed(meeting_id));
create policy decision_insert on atp.decisions for insert to authenticated with check(decided_by=auth.uid() and atp_private.meeting_allowed(meeting_id,true));
alter table atp.drafts enable row level security;
create policy draft_self on atp.drafts for all to authenticated using(owner_id=auth.uid() and atp_private.member_of(org_id)) with check(owner_id=auth.uid() and atp_private.member_of(org_id));
alter table atp.idempotency_keys enable row level security;
create policy idempotency_self on atp.idempotency_keys for all to authenticated using(owner_id=auth.uid()) with check(owner_id=auth.uid());
alter table atp.deletion_jobs enable row level security;
-- A deletion is enqueued before soft-delete in the same transaction.
create policy deletion_insert on atp.deletion_jobs for insert to authenticated with check(requested_by=auth.uid() and atp_private.meeting_owned(meeting_id));
alter table atp.deletion_tombstones enable row level security;
alter table atp.integration_events enable row level security;
alter table atp.outbox enable row level security;
create policy outbox_read on atp.outbox for select to authenticated using(atp_private.meeting_allowed(meeting_id));

-- Explicit grants; no anonymous access or ordinary user access to worker payload writes.
grant select on atp.memberships,atp.meetings,atp.shares,atp.participants,atp.profile_versions,atp.runs,atp.jobs,atp.briefings,atp.sources,atp.facts,atp.decisions,atp.drafts,atp.idempotency_keys,atp.outbox to authenticated;
grant insert on atp.meetings,atp.shares,atp.participants,atp.profile_versions,atp.runs,atp.jobs,atp.decisions,atp.drafts,atp.idempotency_keys,atp.deletion_jobs to authenticated;
grant update(input,company_name,scheduled_at,updated_at,deleted_at) on atp.meetings to authenticated;
grant update(clarified_input,status,phase,version,clarifications,answers,updated_at,pdf_status) on atp.runs to authenticated;
grant update(status,lease_token,lease_until,updated_at) on atp.jobs to authenticated;
grant update(pdf_path,pdf_error,pdf_status) on atp.briefings to authenticated;
grant delete on atp.shares,atp.participants to authenticated;
grant all on all tables in schema atp to atp_worker;

create function atp_private.immutable_run_snapshot() returns trigger
language plpgsql set search_path = '' as $$ begin
  if old.input_snapshot is distinct from new.input_snapshot or old.profile is distinct from new.profile
    or old.profile_hash is distinct from new.profile_hash or old.profile_version is distinct from new.profile_version
    or old.meeting_id is distinct from new.meeting_id or old.org_id is distinct from new.org_id then
    raise exception 'IMMUTABLE_SNAPSHOT';
  end if; return new;
end $$;
revoke all on function atp_private.immutable_run_snapshot() from public;
create trigger immutable_run_snapshot before update on atp.runs for each row execute function atp_private.immutable_run_snapshot();

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('briefings','briefings',false,10485760,array['application/pdf']) on conflict(id) do update set public=false;
-- The API streams after a fresh object authorization. The storage policy also restricts JWT reads.
create policy atp_private_pdf_read on storage.objects for select to authenticated using(
  bucket_id='briefings' and case
    when (storage.foldername(name))[2] ~ '^[0-9a-fA-F-]{36}$'
    then atp_private.meeting_allowed(((storage.foldername(name))[2])::uuid)
    else false end
);
