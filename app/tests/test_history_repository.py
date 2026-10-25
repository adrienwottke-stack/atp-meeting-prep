"""History summaries through actual local Postgres/RLS and worker checkpoints."""

from uuid import uuid4

from test_repository import create
from test_repository import database_case as database_case


def test_history_worker_progress_and_read_only_share(database_case):
    c = database_case
    c.settings.worker_queue = "history-" + str(uuid4())
    first = create(c)
    owner, reader = c.actors
    assert c.repo.list_meetings(reader) == []
    job = c.repo.claim_job("history-test")
    assert job["run_id"] == first["run_id"]
    assert c.repo.checkpoint(job["id"], job["lease_token"], "exporting", {}, {"active_seconds": 460})
    c.admin.execute(
        "insert into atp.shares(meeting_id,user_id,permission,granted_by) values(%s,%s,'read',%s)",
        (first["meeting_id"], reader.user_id, owner.user_id),
    )
    summary = c.repo.list_meetings(reader)[0]["runs"][0]
    assert summary["phase"] == "exporting"
    assert summary["timing_state"] == "running"
    assert 0 <= summary["estimated_remaining_seconds"] <= 20
    assert not {"job_usage", "lease_token", "worker_id", "checkpoint", "usage_saved_at", "lease_until"} & summary.keys()
    c.admin.execute("update atp.jobs set lease_until=now()-interval '1 second' where id=%s", (job["id"],))
    summary = c.repo.list_meetings(owner)[0]["runs"][0]
    assert summary["timing_state"] == "interrupted"
    assert summary["estimated_remaining_seconds"] is None
    c.admin.execute("delete from atp.shares where meeting_id=%s", (first["meeting_id"],))
    assert c.repo.list_meetings(reader) == []
