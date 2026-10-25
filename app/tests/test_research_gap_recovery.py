"""Real isolated Postgres tests for adopting the no-research-homework policy."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_clarification_lifecycle import CountedProvider
from test_repository import create
from test_repository import database_case as database_case

from atp.models import InputAssessment, MeetingInput, ResearchQuery
from atp.storage import Storage
from atp.workflow import Workflow


def pause(case, *, real_question=False, checkpoint=None):
    case.settings.worker_queue = "research-gap-adoption-" + str(uuid4())
    created = create(case)
    job = case.repo.claim_job("older-worker")
    questions = [
        {
            "question_id": "K06:employees_total",
            "code": "K06",
            "field": "employees_total",
            "reason": "Not found",
            "options": [],
            "answer": None,
            "handling": "blocking_dependent_output",
        }
    ]
    if real_question:
        questions.append(
            {
                "question_id": "K04:goal",
                "code": "K04",
                "field": "goal",
                "reason": "Confirm goal",
                "options": [],
                "answer": None,
                "handling": "clarify",
            }
        )
    assert case.repo.pause_job(
        job["id"],
        job["lease_token"],
        questions,
        checkpoint or {"saved_research": "preserve"},
        {"model_requests": 1, "active_seconds": 3},
    )
    return created


def test_old_research_pause_resumes_once_preserving_input_answers_budget_and_checkpoint(database_case):
    c = database_case
    created = pause(c)
    before = c.repo.get_run(c.actors[0], created["run_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: c.repo.resume_research_gaps(), range(2)))
    assert sum(counts) == 1 and c.repo.resume_research_gaps() == 0
    after = c.repo.get_run(c.actors[0], created["run_id"])
    assert after["status"] == "queued" and after["version"] == before["version"] + 1
    assert after["answers"] == before["answers"] == {}
    assert after["input"] == before["input"] and after["usage"] == before["usage"]
    job = c.repo.claim_job("new-worker")
    assert job["run_id"] == created["run_id"] and job["checkpoint"]["saved_research"] == "preserve"
    assert job["usage"]["model_requests"] == 1 and job["usage"]["active_seconds"] == 3
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        c.repo.answer(c.actors[0], created["run_id"], before["version"], {"K06:employees_total": "120"})


def test_mixed_batch_keeps_real_question_until_user_confirms_it(database_case):
    c = database_case
    created = pause(c, real_question=True)
    before = c.repo.get_run(c.actors[0], created["run_id"])
    assert c.repo.resume_research_gaps() == 0 and c.repo.claim_job("not-ready") is None
    c.repo.answer(c.actors[0], created["run_id"], before["version"], {"K04:goal": "Agree next steps"})
    job = c.repo.claim_job("after-answer")
    assert job["input"]["goal"] == "Agree next steps"
    assert set(job["answers"]) == {"K04:goal"}
    assert job["answers"]["K04:goal"]["confirmed_by"] == c.actors[0].user_id
    assert job["clarifications"][0]["answer"] == "Agree next steps"


@pytest.mark.parametrize("guard", ["other_queue", "deleted"])
def test_recovery_honors_queue_and_deletion_guards(database_case, guard):
    c = database_case
    created = pause(c)
    if guard == "other_queue":
        c.settings.worker_queue = "unrelated-queue"
    else:
        c.repo.delete_meeting(c.actors[0], created["meeting_id"])
    assert c.repo.resume_research_gaps() == 0


def test_recovered_pause_finishes_from_saved_research_without_repeating_provider_work(database_case):
    c = database_case
    c.settings.output_dir = c.tmp_path / "pdfs"
    provider = CountedProvider(c.settings)
    bundle = asyncio.run(
        provider.research(ResearchQuery.from_input(MeetingInput.model_validate(c.data)), None)
    )
    created = pause(
        c,
        checkpoint={
            "research": bundle.model_dump(mode="json"),
            "evidence_reviewed": True,
            "input_assessment": InputAssessment(evaluated=True).model_dump(mode="json"),
            "clarification_rounds": 1,
        },
    )
    provider = CountedProvider(c.settings)
    job = c.repo.claim_job("new-policy-worker")
    workflow = Workflow(c.settings, c.repo, Storage(c.settings))
    workflow.provider = provider
    asyncio.run(workflow.process(job))
    final = c.repo.get_run(c.actors[0], created["run_id"])
    assert final["status"] == "partial"
    result = c.repo.get_briefing(c.actors[0], final["briefing_id"])
    assert result["quality"]["state"] == "limited" and result["resolutions"] == {}
    assert provider.calls == {"assessment": 0, "research": 0, "review": 0, "synthesis": 1}
