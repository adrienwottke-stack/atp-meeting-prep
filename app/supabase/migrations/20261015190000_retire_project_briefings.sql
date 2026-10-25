-- Retire generation, retain historical rows and their original expiry deadlines.
-- Stop old web/worker processes before applying; start only the new release afterwards.
update atp.memory_tasks
set status='cancelled', lease_token=null, lease_until=null
where kind='project' and status in ('queued','running');

-- NOT VALID preserves historical rows while rejecting new project tasks (including worker/direct SQL).
alter table atp.memory_tasks add constraint memory_tasks_learning_only
check (kind='learning') not valid;

-- Existing task references remain readable as historical feedback, but cannot be added again.
create function atp_private.reject_retired_project_feedback() returns trigger
language plpgsql set search_path='' as $$
begin
 if new.task_id is not null then
   raise exception 'Project briefing feedback has been retired' using errcode='23514';
 end if;
 return new;
end $$;
create trigger memory_feedback_no_retired_task before insert on atp.memory_feedback
for each row execute function atp_private.reject_retired_project_feedback();
revoke all on function atp_private.reject_retired_project_feedback() from public,anon,authenticated;
