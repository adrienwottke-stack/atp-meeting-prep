"""Retirement and legacy retention against the isolated local PostgreSQL backend."""

from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from retirement_fixtures import historical_tasks
from test_repository import database_case as database_case

from atp.memory_models import ContextInput, EntryInput, FeedbackInput
from atp.memory_store import PostgresMemoryStore
from atp.memory_workflow import MemoryWorkflow
from atp.storage import Storage


@pytest.fixture
def memory_case(database_case):
    c = database_case
    c.settings.memory_enabled = True
    c.settings.retention_days = 30
    c.settings.memory_user_ids = ""
    store = PostgresMemoryStore(c.repo)
    context = store.save_context(c.actors[0], ContextInput(name="Retirement fixture", kind="project"))
    try:
        yield c, store, context
    finally:
        c.admin.execute("delete from atp.memory_tasks where org_id=%s", (c.org,))
        c.admin.execute("delete from atp.memory_contexts where org_id=%s", (c.org,))


def legacy_task(c, context, status="completed", pdf="legacy/synthetic.pdf", allow_history=True):
    task_id = str(uuid4())
    with historical_tasks(c.admin) if allow_history else nullcontext():
        c.admin.execute(
            "insert into atp.memory_tasks(id,org_id,created_by,context_id,kind,input,memory_snapshot,"
            "idempotency_key,payload_hash,queue,expires_at,status,pdf_path,result,lease_token,lease_until) "
            "values(%s,%s,%s,%s,'project','{}','{}',%s,'synthetic',%s,now()+interval '2 days',%s,%s,'{}',%s,now()+interval '60 seconds')",
            (
                task_id,
                c.org,
                c.actors[0].user_id,
                context["id"],
                str(uuid4()),
                c.settings.worker_queue,
                status,
                pdf,
                str(uuid4()),
            ),
        )
    return task_id


async def test_learning_workflow_and_legacy_api_boundary(memory_case):
    c, store, context = memory_case
    old = legacy_task(c, context)
    with pytest.raises(ValueError, match="NOT_FOUND"):
        store.start_task(c.actors[0], "project", {}, str(uuid4()))
    with pytest.raises(ValueError, match="NOT_FOUND"):
        store.tasks(c.actors[0], old)
    feedback = store.feedback(
        c.actors[0], FeedbackInput(context_id=context["id"], text="Vorbereitung war hilfreich")
    )
    payload = {"feedback_id": feedback["id"], "target_context_id": context["id"]}
    key = str(uuid4())
    task = store.start_task(c.actors[0], "learning", payload, key)
    assert store.start_task(c.actors[0], "learning", payload, key)["id"] == task["id"]
    assert [t["id"] for t in store.tasks(c.actors[0])] == [task["id"]]
    with pytest.raises(ValueError, match="NOT_FOUND"):
        store.tasks(c.actors[1], task["id"])
    workflow = MemoryWorkflow(c.settings, c.repo, Storage(c.settings))
    claimed = workflow.claim()
    assert claimed["id"] == task["id"]
    await workflow.process(claimed)
    result = store.tasks(c.actors[0], task["id"])
    assert result["status"] == "completed" and result["pdf_path"] is None
    entry = store.confirm_learning(
        c.actors[0],
        task["id"],
        EntryInput(text="Zuerst den bestätigten Stand klären", kind="learning", status="confirmed"),
    )
    assert entry["kind"] == "learning"


def test_expired_legacy_task_keeps_context_and_unrelated_learning(memory_case):
    c, store, context = memory_case
    old = legacy_task(c, context)
    feedback = store.feedback(c.actors[0], FeedbackInput(context_id=context["id"], text="Gültiges Feedback"))
    learning = store.start_task(
        c.actors[0],
        "learning",
        {"feedback_id": feedback["id"], "target_context_id": context["id"]},
        str(uuid4()),
    )
    store.save_entry(c.actors[0], context["id"], EntryInput(text="Gültiges Wissen", status="confirmed"))
    with c.repo.connection(worker=True) as db:
        store.expire(db)
    assert c.admin.execute("select id from atp.memory_tasks where id=%s", (old,)).fetchone()
    with historical_tasks(c.admin):
        c.admin.execute("update atp.memory_tasks set expires_at=now()-interval '1 second' where id=%s", (old,))
    with c.repo.connection(worker=True) as db:
        store.expire(db)
    assert c.admin.execute("select id from atp.memory_tasks where id=%s", (old,)).fetchone() is None
    assert store.context(c.actors[0], context["id"])["entries"][0]["text"] == "Gültiges Wissen"
    assert store.tasks(c.actors[0], learning["id"])
    assert (
        c.admin.execute("select path from atp.storage_cleanup where org_id=%s", (c.org,)).fetchone()[0]
        == "legacy/synthetic.pdf"
    )


async def test_learning_resumes_checkpoint_and_old_lease_cannot_publish(memory_case):
    c, store, context = memory_case
    feedback = store.feedback(c.actors[0], FeedbackInput(context_id=context["id"], text="Checkpoint fixture"))
    task = store.start_task(
        c.actors[0],
        "learning",
        {"feedback_id": feedback["id"], "target_context_id": context["id"]},
        str(uuid4()),
    )
    worker = MemoryWorkflow(c.settings, c.repo, Storage(c.settings))
    old = worker.claim()
    assert worker.save(
        old,
        usage={"model_requests": 1},
        checkpoint={"narrative": worker.fixture(old).model_dump(mode="json")},
    )
    c.admin.execute(
        "update atp.memory_tasks set lease_until=now()-interval '1 second' where id=%s", (task["id"],)
    )
    recovered = worker.claim()
    assert recovered["lease_token"] != old["lease_token"]
    assert not worker.save(old, result={"invalid": True})
    await worker.process(recovered)
    result = store.tasks(c.actors[0], task["id"])
    assert result["status"] == "completed"
    assert result["result"]["usage"]["model_requests"] == 1


def test_migration_cancels_only_active_projects_and_blocks_new_inserts(memory_case):
    c, _, context = memory_case
    active = legacy_task(c, context, "running")
    complete = legacy_task(c, context)
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20261015190000_retire_project_briefings.sql"
    )
    # Roll back DDL after this test: other tests may seed historical rows on the pre-upgrade schema.
    with c.admin.transaction(force_rollback=True):
        c.admin.execute("alter table atp.memory_tasks drop constraint if exists memory_tasks_learning_only")
        c.admin.execute("drop trigger if exists memory_feedback_no_retired_task on atp.memory_feedback")
        c.admin.execute("drop function if exists atp_private.reject_retired_project_feedback()")
        c.admin.execute(migration.read_text(encoding="utf-8"))
        row = c.admin.execute(
            "select status,lease_token,lease_until,pdf_path from atp.memory_tasks where id=%s", (active,)
        ).fetchone()
        assert row == ("cancelled", None, None, "legacy/synthetic.pdf")
        assert (
            c.admin.execute("select status from atp.memory_tasks where id=%s", (complete,)).fetchone()[0]
            == "completed"
        )
        with pytest.raises(psycopg.errors.CheckViolation), c.admin.transaction():
            legacy_task(c, context, allow_history=False)
        with pytest.raises(psycopg.errors.CheckViolation), c.admin.transaction():
            c.admin.execute(
                "insert into atp.memory_feedback(context_id,org_id,created_by,kind,text,task_id,expires_at) values(%s,%s,%s,'helpful','retired',%s,now()+interval '1 day')",
                (context["id"], c.org, c.actors[0].user_id, complete),
            )
