-- Cleanup survives parent deletion and retries uncertain upload/delete outcomes.
create table atp.storage_cleanup (
  path text primary key,
  meeting_id uuid not null,
  org_id uuid not null,
  run_id uuid not null,
  attempt int not null default 0,
  next_attempt timestamptz not null default now(),
  created_at timestamptz not null default now()
);
alter table atp.storage_cleanup enable row level security;
grant all on atp.storage_cleanup to atp_worker;
create index storage_cleanup_due on atp.storage_cleanup(next_attempt);

-- result_version is meeting-local, so unrelated meetings must not collide at one CRM record.
alter table atp.outbox drop constraint outbox_target_system_target_record_id_result_version_key;
alter table atp.outbox add constraint outbox_meeting_target_version_key
  unique(meeting_id,target_system,target_record_id,result_version);

drop policy atp_private_pdf_read on storage.objects;
create policy atp_private_pdf_read on storage.objects for select to authenticated using(
  bucket_id='briefings' and case
    when (storage.foldername(name))[2] ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    then atp_private.meeting_allowed(((storage.foldername(name))[2])::uuid)
    else false end
);
