-- Enforce parent organisation and result identity in the database as well as Python.
alter table atp.meetings add constraint meeting_org_key unique(id,org_id);
alter table atp.runs add constraint run_org_key unique(id,org_id);
alter table atp.runs add constraint run_meeting_key unique(id,meeting_id);
alter table atp.runs add constraint run_meeting_org foreign key(meeting_id,org_id) references atp.meetings(id,org_id) on delete cascade;
alter table atp.jobs add constraint job_run_org foreign key(run_id,org_id) references atp.runs(id,org_id) on delete cascade;
alter table atp.briefings add constraint briefing_run_meeting foreign key(run_id,meeting_id) references atp.runs(id,meeting_id) on delete cascade;
alter table atp.briefings add constraint briefing_meeting_org foreign key(meeting_id,org_id) references atp.meetings(id,org_id) on delete cascade;
alter table atp.decisions add constraint decision_run_meeting foreign key(run_id,meeting_id) references atp.runs(id,meeting_id) on delete cascade;
alter table atp.deletion_jobs add constraint deletion_meeting_org foreign key(meeting_id,org_id) references atp.meetings(id,org_id) on delete cascade;

-- Direct owner predicate also permits INSERT ... RETURNING without a recursive lookup.
drop policy meeting_read on atp.meetings;
create policy meeting_read on atp.meetings for select to authenticated using(
  deleted_at is null and atp_private.member_of(org_id)
  and (owner_id=auth.uid() or atp_private.meeting_allowed(id))
);

create function atp_private.record_deletion() returns trigger
language plpgsql security definer set search_path='' as $$ begin
  if new.deleted_at is not null and old.deleted_at is null then
    if auth.uid() is not null and auth.uid()<>old.owner_id then
      raise exception 'OWNER_REQUIRED';
    end if;
    insert into atp.deletion_tombstones(meeting_id,org_id,requested_at)
      values(old.id,old.org_id,new.deleted_at) on conflict do nothing;
  end if;
  return new;
end $$;
revoke all on function atp_private.record_deletion() from public,anon,authenticated;
create trigger record_deletion after update of deleted_at on atp.meetings
  for each row execute function atp_private.record_deletion();

create function atp_private.immutable_profile() returns trigger
language plpgsql set search_path='' as $$ begin
  if new.content is distinct from old.content or new.content_hash is distinct from old.content_hash
    or new.version is distinct from old.version or new.org_id is distinct from old.org_id then
    raise exception 'IMMUTABLE_PROFILE';
  end if;
  return new;
end $$;
revoke all on function atp_private.immutable_profile() from public;
create trigger immutable_profile before update on atp.profile_versions
  for each row execute function atp_private.immutable_profile();

create index memberships_org on atp.memberships(org_id,user_id) where active;
create index shares_user on atp.shares(user_id,meeting_id);
create index meetings_owner on atp.meetings(owner_id,created_at desc) where deleted_at is null;
create index runs_meeting_created on atp.runs(meeting_id,created_at desc);
create index decisions_meeting_run on atp.decisions(meeting_id,run_id,created_at desc);
create index drafts_created on atp.drafts(created_at);
