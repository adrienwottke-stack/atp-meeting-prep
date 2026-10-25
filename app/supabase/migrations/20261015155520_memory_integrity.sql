-- Preserve tenant identity even for direct authenticated table access.
alter table atp.memory_contexts add constraint memory_context_org_key unique(id,org_id);
alter table atp.memory_entries add constraint memory_entry_context_org foreign key(context_id,org_id) references atp.memory_contexts(id,org_id) on delete cascade;
alter table atp.memory_tasks add constraint memory_task_context_org foreign key(context_id,org_id) references atp.memory_contexts(id,org_id);
alter table atp.memory_feedback add constraint memory_feedback_context_org foreign key(context_id,org_id) references atp.memory_contexts(id,org_id) on delete cascade;
-- Snapshots are written once at run creation. No later operation can replace their evidence.
create or replace function atp_private.immutable_run_snapshot() returns trigger
language plpgsql set search_path='' as $$ begin
 if old.input_snapshot is distinct from new.input_snapshot or old.profile is distinct from new.profile
 or old.profile_hash is distinct from new.profile_hash or old.profile_version is distinct from new.profile_version
 or old.meeting_id is distinct from new.meeting_id or old.org_id is distinct from new.org_id
 or old.memory_snapshot is distinct from new.memory_snapshot then raise exception 'IMMUTABLE_SNAPSHOT'; end if;
 return new;
end $$;
revoke all on function atp_private.immutable_run_snapshot() from public,anon;
