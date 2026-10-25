-- Cache only the statement-constant caller identity. Row-dependent ACL helpers
-- remain correlated to each row; policy commands, roles and grants are unchanged.
alter policy membership_self on atp.memberships
  using(user_id=(select auth.uid()));
alter policy meeting_insert on atp.meetings
  with check(owner_id=(select auth.uid()) and atp_private.member_of(org_id));
alter policy share_insert on atp.shares
  with check(atp_private.meeting_owned(meeting_id) and granted_by=(select auth.uid()));
alter policy run_insert on atp.runs
  with check(atp_private.meeting_allowed(meeting_id,true)
    and created_by=(select auth.uid()) and atp_private.member_of(org_id));
alter policy decision_insert on atp.decisions
  with check(decided_by=(select auth.uid()) and atp_private.meeting_allowed(meeting_id,true));
alter policy draft_self on atp.drafts
  using(owner_id=(select auth.uid()) and atp_private.member_of(org_id))
  with check(owner_id=(select auth.uid()) and atp_private.member_of(org_id));
alter policy idempotency_self on atp.idempotency_keys
  using(owner_id=(select auth.uid()))
  with check(owner_id=(select auth.uid()));
alter policy deletion_insert on atp.deletion_jobs
  with check(requested_by=(select auth.uid()) and atp_private.meeting_owned(meeting_id));
alter policy share_update on atp.shares
  using(atp_private.meeting_owned(meeting_id))
  with check(atp_private.meeting_owned(meeting_id) and granted_by=(select auth.uid()));

-- Previously SELECT evaluated read_allowed OR edit_allowed. Every edit-allowed
-- meeting is also read-allowed by the same helper, so participant_read alone
-- preserves that SELECT set. Keep each write predicate exactly as before.
drop policy participant_write on atp.participants;
create policy participant_insert on atp.participants for insert to authenticated
  with check(atp_private.meeting_allowed(meeting_id,true));
create policy participant_update on atp.participants for update to authenticated
  using(atp_private.meeting_allowed(meeting_id,true))
  with check(atp_private.meeting_allowed(meeting_id,true));
create policy participant_delete on atp.participants for delete to authenticated
  using(atp_private.meeting_allowed(meeting_id,true));

-- Complete, non-partial indexes for foreign-key checks/cascades. The existing
-- meetings_owner index excludes soft-deleted rows, so it cannot cover the FK.
create index meetings_owner_fk on atp.meetings(owner_id);
create index shares_granted_by_fk on atp.shares(granted_by);
create index profile_versions_activated_by_fk on atp.profile_versions(activated_by);
create index runs_created_by_fk on atp.runs(created_by);
create index drafts_owner_fk on atp.drafts(owner_id);
create index decisions_decided_by_fk on atp.decisions(decided_by);
create index deletion_jobs_requested_by_fk on atp.deletion_jobs(requested_by);
create index outbox_approved_by_fk on atp.outbox(approved_by);
create index profile_activations_activated_by_fk on atp.profile_activations(activated_by);

-- Existing indexes already cover memberships.user_id, shares.user_id and
-- meeting_id, participants.meeting_id, sources/facts.run_id, idempotency owner_id,
-- and outbox.meeting_id. Do not add duplicate single-column indexes for these.
create index idempotency_keys_meeting_fk on atp.idempotency_keys(meeting_id);
create index idempotency_keys_run_fk on atp.idempotency_keys(run_id);
create index decisions_run_fk on atp.decisions(run_id);

-- Cover composite FKs where the prefix can identify multiple rows. Existing
-- unique run_id indexes on jobs/briefings and the deletion_jobs meeting_id PK
-- already narrow their FK checks to one row; adding composite indexes there
-- would only silence an informational advisor notice without helping lookup.
create index runs_meeting_org_fk on atp.runs(meeting_id,org_id);
create index briefings_meeting_org_fk on atp.briefings(meeting_id,org_id);
create index profile_activations_profile_fk on atp.profile_activations(org_id,version);

-- decisions_meeting_run(meeting_id,run_id,created_at) already covers the composite
-- decision_run_meeting FK. No index is removed or replaced in this migration.
