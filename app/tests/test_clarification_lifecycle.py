"""Persisted clarification rounds and restart behavior with controlled research."""

import asyncio
from uuid import uuid4

import pytest
from test_repository import create
from test_repository import database_case as database_case

from atp.models import Fact, InputAssessment, ResearchBundle, Source
from atp.providers import FixtureProvider
from atp.storage import Storage
from atp.workflow import Workflow


class CountedProvider(FixtureProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = {"assessment": 0, "research": 0, "review": 0, "synthesis": 0}

    async def assess_input(self, data, budget):
        self.calls["assessment"] += 1
        return InputAssessment(evaluated=True)

    async def research(self, query, budget):
        self.calls["research"] += 1
        return ResearchBundle(
            sources=[
                Source(
                    source_id="controlled-source",
                    url="https://integration.example",
                    title="Controlled company fixture",
                    verified=True,
                )
            ],
            facts=[
                Fact(
                    fact_id="controlled-business",
                    field="business_model",
                    value="Baugruppenfertigung",
                    status="verified",
                    provenance="web_verified",
                    evidence_ids=["controlled-source"],
                )
            ],
            gaps=["Controlled fixture: qualification and personal background unavailable."],
        )

    async def review_evidence(self, query, bundle, budget):
        self.calls["review"] += 1
        return bundle

    async def synthesize(self, *args):
        self.calls["synthesis"] += 1
        return await super().synthesize(*args)


def advance(case, provider):
    # Construct a new worker object each time: all state must come from Postgres.
    workflow = Workflow(case.settings, case.repo, Storage(case.settings))
    workflow.provider = provider
    job = case.repo.claim_job("lifecycle-" + str(uuid4()))
    assert job
    asyncio.run(workflow.process(job))
    return case.repo.get_run(case.actors[0], job["run_id"])


def test_missing_research_finishes_without_user_questions_or_invented_answers(database_case):
    c = database_case
    c.settings.worker_queue = "research-without-homework-" + str(uuid4())
    c.settings.output_dir = c.tmp_path / "pdfs"
    created = create(c)
    provider = CountedProvider(c.settings)
    final = advance(c, provider)
    assert final["status"] == "partial"
    result = c.repo.get_briefing(c.actors[0], final["briefing_id"])
    assert result["quality"]["state"] == "limited"
    assert result["resolutions"] == {}
    assert result["icp"]["status"] == "unclear"
    assert provider.calls == {"assessment": 1, "research": 1, "review": 1, "synthesis": 1}
    with c.repo.connection(worker=True) as db:
        checkpoint = db.execute(
            "select checkpoint from atp.jobs where run_id=%s", (created["run_id"],)
        ).fetchone()["checkpoint"]
    assert checkpoint.get("clarification_rounds", 0) == 0
    assert not checkpoint["clarification_plan"]["exhausted"]
    assert len(checkpoint["clarification_plan"]["open_questions"]) == 5
    assert checkpoint["clarification_plan"]["questions"] == []


@pytest.mark.parametrize("website", ["javascript:alert(1)", "http://127.0.0.1:8000", "not-a-website"])
def test_invalid_required_website_answer_cannot_bypass_preflight(database_case, website):
    c = database_case
    c.settings.worker_queue = "domain-preflight-" + str(uuid4())
    c.settings.output_dir = c.tmp_path / "pdfs"
    c.data["company"]["website"] = ""
    created = create(c)
    provider = CountedProvider(c.settings)
    first = advance(c, provider)
    assert [q["question_id"] for q in first["clarifications"]] == ["K01:company.website"]
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        c.repo.answer(
            c.actors[0],
            created["run_id"],
            first["version"],
            {"K01:company.website": {"action": "provide", "value": website}},
        )
    unchanged = c.repo.get_run(c.actors[0], created["run_id"])
    assert unchanged["status"] == "needs_clarification"
    assert unchanged["input"]["company"]["website"] == ""
    assert provider.calls == {"assessment": 0, "research": 0, "review": 0, "synthesis": 0}
