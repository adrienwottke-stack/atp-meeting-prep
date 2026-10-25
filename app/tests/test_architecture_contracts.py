"""Architecture guarantees verified against real isolated local Postgres."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from retirement_fixtures import historical_tasks
from test_repository import create
from test_repository import database_case as database_case

from atp.memory_models import ContextInput, EntryInput, FeedbackInput
from atp.memory_store import PostgresMemoryStore
from atp.memory_workflow import MemoryWorkflow
from atp.storage import Storage


def isolate(case):
    case.settings.worker_queue = "domain-architecture-" + str(uuid4())
    case.settings.org_concurrency = 1
    return case


def test_new_run_retries_same_key_but_new_keys_create_distinct_versions(database_case):
    c = isolate(database_case)
    first = create(c)
    key = str(uuid4())
    second = c.repo.new_run(c.actors[0], first["meeting_id"], c.data, c.profile, key)
    retry = c.repo.new_run(c.actors[0], first["meeting_id"], c.data, c.profile, key)
    assert second == retry
    assert second["result_version"] == 2
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        c.repo.new_run(c.actors[0], first["meeting_id"], {**c.data, "goal": "Changed"}, c.profile, key)
    third = c.repo.new_run(c.actors[0], first["meeting_id"], c.data, c.profile, str(uuid4()))
    assert third["result_version"] == 3
    assert third["run_id"] != second["run_id"]
    assert (
        c.admin.execute(
            "select count(*) from atp.runs where meeting_id=%s", (first["meeting_id"],)
        ).fetchone()[0]
        == 3
    )


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_new_run_serializes_idempotency_and_version_allocation(database_case, same_key):
    c = isolate(database_case)
    first = create(c)
    count, barrier, shared_key = 6, Barrier(6), str(uuid4())

    def start(index):
        barrier.wait(timeout=10)
        key = shared_key if same_key else shared_key + ":" + str(index)
        return c.repo.new_run(c.actors[0], first["meeting_id"], c.data, c.profile, key)

    with ThreadPoolExecutor(max_workers=count) as pool:
        runs = list(pool.map(start, range(count)))
    assert len({r["run_id"] for r in runs}) == (1 if same_key else count)
    assert {r["result_version"] for r in runs} == ({2} if same_key else set(range(2, count + 2)))
    assert c.admin.execute(
        "select count(*) from atp.runs where meeting_id=%s", (first["meeting_id"],)
    ).fetchone()[0] == (2 if same_key else count + 1)


def test_history_batches_run_loading_in_two_queries(database_case, monkeypatch):
    c = isolate(database_case)
    first = create(c)
    c.repo.new_run(c.actors[0], first["meeting_id"], c.data, c.profile, str(uuid4()))
    for _ in range(6):
        create(c)
    original = c.repo._connection
    queries = []

    class CountedConnection:
        def __init__(self, db):
            self.db = db

        def execute(self, query, *args, **kwargs):
            queries.append(str(query))
            return self.db.execute(query, *args, **kwargs)

    @contextmanager
    def counted(*args, **kwargs):
        with original(*args, **kwargs) as db:
            yield CountedConnection(db)

    monkeypatch.setattr(c.repo, "_connection", counted)
    meetings = c.repo.list_meetings(c.actors[0])
    assert len(meetings) == 7
    assert len(queries) == 2
    item = next(m for m in meetings if m["meeting_id"] == first["meeting_id"])
    assert [r["result_version"] for r in item["runs"]] == [2, 1]
    assert item["latest_run_id"] == item["runs"][0]["run_id"]
    assert item["runs"][0]["phase"] == "validating"
    assert item["runs"][0]["timing_state"] == "queued"
    assert item["runs"][0]["estimated_remaining_seconds"] is None
    assert not {"job_status", "job_usage", "lease_until", "usage_saved_at"} & item["runs"][0].keys()


def memory_case(c):
    c.settings.memory_enabled = True
    c.settings.retention_days = 30
    store = PostgresMemoryStore(c.repo)
    context = store.save_context(
        c.actors[0], ContextInput(name="Controlled architecture project", kind="project")
    )
    feedback = store.feedback(
        c.actors[0], FeedbackInput(context_id=context["id"], text="Controlled architecture feedback")
    )
    task = store.start_task(
        c.actors[0],
        "learning",
        {"target_context_id": context["id"], "feedback_id": feedback["id"]},
        str(uuid4()),
    )
    return context, task, MemoryWorkflow(c.settings, c.repo, Storage(c.settings))


def clear_memory(c):
    c.admin.execute("delete from atp.memory_tasks where org_id=%s", (c.org,))
    c.admin.execute("delete from atp.memory_contexts where org_id=%s", (c.org,))


async def test_learning_completes_while_legacy_project_tasks_stay_inaccessible(database_case):
    c = isolate(database_case)
    try:
        context, task, memory = memory_case(c)
        retired_id = str(uuid4())
        with historical_tasks(c.admin):
            c.admin.execute(
                "insert into atp.memory_tasks(id,org_id,created_by,context_id,kind,input,memory_snapshot,"
                "idempotency_key,payload_hash,queue,expires_at) select %s,org_id,created_by,context_id,"
                "'project',input,memory_snapshot,%s,payload_hash,queue,expires_at from atp.memory_tasks where id=%s",
                (retired_id, str(uuid4()), task["id"]),
            )
        job = memory.claim()
        assert job["id"] == task["id"]
        await memory.process(job)
        store = c.repo.memory()
        result = store.tasks(c.actors[0], task["id"])
        assert result["status"] == "completed"
        assert result["result"]["narrative"]["text"]
        assert result["usage"]["search_calls"] == 0
        entry = store.confirm_learning(
            c.actors[0],
            task["id"],
            EntryInput(text=result["result"]["narrative"]["text"], kind="learning", status="confirmed"),
        )
        assert entry["context_id"] == context["id"]
        assert memory.claim() is None
        assert all(item["kind"] == "learning" for item in store.tasks(c.actors[0]))
        with pytest.raises(ValueError, match="NOT_FOUND"):
            store.tasks(c.actors[0], retired_id)
    finally:
        clear_memory(c)


@pytest.mark.parametrize("first_kind", ["company", "memory"])
def test_active_company_and_memory_jobs_share_organization_capacity(database_case, first_kind):
    c = isolate(database_case)
    create(c)
    try:
        _, task, memory = memory_case(c)
        if first_kind == "company":
            company = c.repo.claim_job("architecture-company")
            assert company
            assert memory.claim() is None
            c.admin.execute(
                "update atp.jobs set status='completed',lease_until=null where id=%s", (company["id"],)
            )
            assert memory.claim()["id"] == task["id"]
        else:
            assert memory.claim()["id"] == task["id"]
            assert c.repo.claim_job("architecture-company") is None
            c.admin.execute(
                "update atp.memory_tasks set status='completed',lease_until=null where id=%s", (task["id"],)
            )
            assert c.repo.claim_job("architecture-company")
    finally:
        clear_memory(c)


def test_concurrent_cross_kind_claims_never_exceed_org_capacity(database_case):
    c = isolate(database_case)
    create(c)
    try:
        _, _, memory = memory_case(c)
        barrier = Barrier(2)

        def company_claim():
            barrier.wait(timeout=10)
            return c.repo.claim_job("architecture-concurrent-company")

        def memory_claim():
            barrier.wait(timeout=10)
            return memory.claim()

        with ThreadPoolExecutor(max_workers=2) as pool:
            company_future, memory_future = pool.submit(company_claim), pool.submit(memory_claim)
            results = [company_future.result(timeout=15), memory_future.result(timeout=15)]
        assert sum(result is not None for result in results) == 1
        company_active = c.admin.execute(
            "select count(*) from atp.jobs where org_id=%s and status='running' and lease_until>now()",
            (c.org,),
        ).fetchone()[0]
        memory_active = c.admin.execute(
            "select count(*) from atp.memory_tasks where org_id=%s and status='running' and lease_until>now()",
            (c.org,),
        ).fetchone()[0]
        assert company_active + memory_active == 1
    finally:
        clear_memory(c)
