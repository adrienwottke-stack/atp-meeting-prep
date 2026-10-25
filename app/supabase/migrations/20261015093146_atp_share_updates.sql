create policy share_update on atp.shares for update to authenticated
  using(atp_private.meeting_owned(meeting_id))
  with check(atp_private.meeting_owned(meeting_id) and granted_by=auth.uid());
grant update(permission,granted_by) on atp.shares to authenticated;
