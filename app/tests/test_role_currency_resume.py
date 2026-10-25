"""A real persisted legacy checkpoint cannot bypass the new currency policy."""

import asyncio
from uuid import uuid4

from psycopg.types.json import Jsonb
from test_repository import create
from test_repository import database_case as database_case
from test_rules import facts

import atp.workflow as workflow_module
from atp.models import Fact, InputAssessment, MeetingInput, OverviewItem, ResearchBundle, Source, Usage
from atp.providers import PROMPT_VERSION, build_limited_result
from atp.rules import evaluate_icp, recommend
from atp.storage import Storage
from atp.workflow import Workflow


def test_resume_old_reviewed_checkpoint_rebuilds_prose_without_repeating_research(database_case, monkeypatch):
    c = database_case
    c.settings.worker_queue = "domain-currency-resume-" + str(uuid4())
    c.settings.output_dir = c.tmp_path / "pdfs"
    created = create(c)
    data = MeetingInput.model_validate(c.data)
    role = Fact(
        fact_id="legacy-current-role",
        subject_ref="person-a",
        field="current_role",
        value="Chief Executive Officer",
        status="verified",
        provenance="web_verified",
        evidence_ids=["s1"],
        information_as_of="2026",
        supporting_quote="Integration GmbH: Person A ist 2026 Chief Executive Officer.",
    )
    bundle = ResearchBundle(
        sources=[
            Source(
                source_id="s1",
                title="Controlled legacy research evidence",
                url="https://integration.example/company",
                verified=True,
                excerpt=role.supporting_quote,
            )
        ],
        facts=[*facts(), role],
    )
    icp = evaluate_icp(bundle.facts, data, c.profile)
    legacy = build_limited_result(
        data,
        bundle,
        c.profile,
        icp,
        recommend(data, icp),
        {**created, "fixture_mode": False},
        Usage(),
        reason=None,
    )
    stale_text = "Person A ist aktuell Chief Executive Officer."
    assert legacy.people[0].status == "confirmed"  # Reproduce the old saved state.
    legacy.prompt_version = "atp-prompts-3"
    legacy.overview = [
        OverviewItem(
            category="people",
            text=stale_text,
            kind="fact",
            fact_ids=[role.fact_id],
            evidence_ids=["s1"],
        )
    ]
    checkpoint = {
        "phase": "generating",
        "input_assessment": InputAssessment(evaluated=True).model_dump(mode="json"),
        "research": bundle.model_dump(mode="json"),
        "evidence_reviewed": True,
        "result": legacy.model_dump(mode="json"),
    }
    previous_usage = {"model_requests": 3, "search_calls": 4, "active_seconds": 12}
    c.admin.execute(
        "update atp.jobs set checkpoint=%s,usage=%s where run_id=%s",
        (Jsonb(checkpoint), Jsonb(previous_usage), created["run_id"]),
    )
    # Exercise the actual nonfixture workflow guard while replacing only model
    # transport. No real provider is created and no model/network call occurs.
    c.settings.fixture_mode = False

    class ControlledProvider:
        calls = {"assessment": 0, "research": 0, "review": 0, "synthesis": 0}

        async def assess_input(self, *args):
            self.calls["assessment"] += 1
            raise AssertionError("Persisted assessment must be reused")

        async def research(self, *args):
            self.calls["research"] += 1
            raise AssertionError("Persisted research must be reused")

        async def review_evidence(self, *args):
            self.calls["review"] += 1
            raise AssertionError("Persisted semantic review must be reused")

        async def synthesize(self, input, current, profile, current_icp, recommendation, meta, budget):
            self.calls["synthesis"] += 1
            return build_limited_result(
                input, current, profile, current_icp, recommendation, meta, budget.usage, reason=None
            )

    provider = ControlledProvider()
    monkeypatch.setattr(workflow_module, "Provider", lambda settings: provider)
    workflow = Workflow(c.settings, c.repo, Storage(c.settings))
    job = c.repo.claim_job("controlled-currency-resume")
    assert job and job["run_id"] == created["run_id"]
    asyncio.run(workflow.process(job))
    run = c.repo.get_run(c.actors[0], created["run_id"])
    assert run["status"] in ("completed", "partial")
    result = c.repo.get_briefing(c.actors[0], run["briefing_id"])
    current_role = next(f for f in result["facts"] if f["fact_id"] == role.fact_id)
    assert current_role["field"] == "undated_role"
    assert current_role["supporting_quote"] == role.supporting_quote
    assert result["prompt_version"] == PROMPT_VERSION
    # A supported identity survives the currency downgrade. The role remains
    # useful context with an explicit caveat, never confirmed current employment.
    assert result["people"][0]["status"] == "confirmed"
    assert "Aktualität ungeklärt" in result["people"][0]["role"]["text"]
    people = next(item for item in result["overview"] if item["category"] == "people")
    assert people["kind"] == "unknown" and "Aktualität offen" in people["text"]
    assert stale_text not in people["text"]
    assert not any(stale_text in text for text in result["summary"])
    assert provider.calls == {"assessment": 0, "research": 0, "review": 0, "synthesis": 1}
    assert result["usage"]["model_requests"] == 3 and result["usage"]["search_calls"] == 4
    assert result["usage"]["active_seconds"] >= 12
    with c.repo.connection(worker=True) as db:
        persisted = db.execute(
            "select checkpoint from atp.jobs where run_id=%s", (created["run_id"],)
        ).fetchone()["checkpoint"]
    assert persisted["evidence_reviewed"]
    assert persisted["result"]["prompt_version"] == PROMPT_VERSION
    assert (
        next(f for f in persisted["research"]["facts"] if f["fact_id"] == role.fact_id)["field"]
        == "undated_role"
    )
