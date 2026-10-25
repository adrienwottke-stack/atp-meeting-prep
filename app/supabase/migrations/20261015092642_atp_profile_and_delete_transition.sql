-- Registry is administrator-managed; an ordinary briefing run can only read it.
drop policy profile_insert on atp.profile_versions;
revoke insert on atp.profile_versions from authenticated;

-- Soft deletion intentionally leaves the user's visible RLS set. This narrow
-- non-exposed capability performs that one transition after its own live ACL check.
create function atp_private.request_deletion(p_id uuid) returns boolean
language plpgsql security definer set search_path='' as $$
declare m atp.meetings;
begin
  if auth.uid() is null then return false; end if;
  select * into m from atp.meetings where id=p_id and owner_id=auth.uid()
    and deleted_at is null and atp_private.member_of(org_id) for update;
  if not found then return false; end if;
  insert into atp.deletion_jobs(meeting_id,org_id,requested_by)
    values(m.id,m.org_id,auth.uid()) on conflict do nothing;
  update atp.jobs set status='cancelled',lease_token=null,lease_until=null,updated_at=now()
    where run_id in(select id from atp.runs where meeting_id=m.id);
  update atp.meetings set deleted_at=now(),updated_at=now() where id=m.id;
  return true;
end $$;
revoke all on function atp_private.request_deletion(uuid) from public,anon;
grant execute on function atp_private.request_deletion(uuid) to authenticated;
revoke update(deleted_at) on atp.meetings from authenticated;
