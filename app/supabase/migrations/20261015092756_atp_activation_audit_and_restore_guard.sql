-- Tombstones are checked for owners as well as shared readers after any restore.
drop policy meeting_read on atp.meetings;
create policy meeting_read on atp.meetings for select to authenticated
  using(atp_private.meeting_allowed(id));

create table atp.profile_activations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null,
  version text not null,
  content_hash text not null,
  activated_by uuid not null references auth.users(id),
  activated_at timestamptz not null default now(),
  reason text not null,
  foreign key(org_id,version) references atp.profile_versions(org_id,version)
);
alter table atp.profile_activations enable row level security;
create policy profile_activation_read on atp.profile_activations for select to authenticated
  using(atp_private.member_of(org_id));
grant select on atp.profile_activations to authenticated;
grant all on atp.profile_activations to atp_worker;
