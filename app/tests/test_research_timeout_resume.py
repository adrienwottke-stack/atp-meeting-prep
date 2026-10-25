"""Local Postgres regression: research cutoff preserves a reviewed partial PDF."""

import asyncio
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_repository import create
from test_repository import database_case as database_case
from test_rules import facts

import atp.workflow as workflow_module
from atp.budget import BudgetExceeded
from atp.models import Fact, InputAssessment, ResearchBundle, Source
from atp.providers import ProviderError, build_limited_result
from atp.storage import Storage
from atp.workflow import Workflow


@pytest.mark.parametrize(
    "has_complete,review_fails,stop",
    [
        (True, False, "hang"),
        (True, True, "timeout"),
        (False, False, "timeout"),
        (True, False, "budget"),
        (True, False, "wrong_query"),
        (True, False, "raw_pending"),
        (False, False, "global_budget"),
        (False, False, "global_timeout"),
    ],
)
def test_research_cutoff_restores_complete_wave_reviews_once_and_resumes_to_pdf(
    database_case,
    monkeypatch,
    has_complete,
    review_fails,
    stop,
):
    c = database_case
    c.settings.worker_queue = "timeout-resume-" + str(uuid4())
    c.settings.output_dir = c.tmp_path / "pdfs"
    c.settings.storage_backend = "local"
    c.data["goal"] = "Konkrete Abläufe kennenlernen und nächsten Schritt klären"
    created = create(c)
    c.admin.execute(
        "update atp.jobs set usage=%s where run_id=%s",
        (
            Jsonb({"active_seconds": 389.4}),
            created["run_id"],
        ),
    )
    source = Source(
        source_id="s1",
        title="Controlled complete wave",
        url="https://integration.example/company",
        excerpt="Integration GmbH fertigt Baugruppen und beschäftigt 120 Mitarbeitende.",
        verified=True,
    )
    completed = ResearchBundle(
        sources=[source],
        facts=[
            *facts(),
            Fact(
                fact_id="complete-business",
                field="business_model",
                value="Baugruppenfertigung",
                status="verified",
                provenance="web_verified",
                evidence_ids=["s1"],
            ),
        ],
    )

    class ControlledProvider:
        calls = {"assessment": 0, "research": 0, "review": 0, "synthesis": 0}

        async def assess_input(self, data, budget):
            self.calls["assessment"] += 1
            return InputAssessment(evaluated=True)

        async def research(self, query, budget, *, checkpoint=None, persist=None):
            self.calls["research"] += 1
            budget.reserve(model=1, searches=4)
            if has_complete:
                progress = {
                    "version": 2,
                    "public_query": query.model_dump(mode="json"),
                    "completed_waves": 1,
                    "bundle": completed.model_dump(mode="json"),
                    "sources": [Source(
                        source_id="pending-source",
                        title="Pending public source",
                        url="https://integration.example/pending",
                        excerpt="UNCHECKED_PENDING_SOURCE_TEXT",
                        verified=True,
                    ).model_dump(mode="json")],
                }
                if stop == "wrong_query":
                    progress["public_query"]["company_name"] = "Different company"
                persist(progress)
                # An unfinished next wave must not mutate the committed bundle,
                # nor replace it with a partial progress message lacking bundle.
                progress["bundle"]["facts"][0]["value"] = "UNCOMMITTED_NEXT_WAVE"
                if stop != "raw_pending":
                    persist({"completed_waves": 1, "partial_next_wave": {"facts": [{"bad": True}]}})
            if stop == "hang":
                await asyncio.Event().wait()  # The actual outer wait_for cancels.
            if stop == "budget":
                raise BudgetExceeded("Controlled research-only budget exhausted")
            if stop in ("global_budget", "global_timeout"):
                budget._base_seconds = budget.settings.deadline_seconds
                if stop == "global_timeout":
                    raise TimeoutError("Controlled total deadline timed out")
                raise BudgetExceeded("Controlled total deadline exhausted")
            raise TimeoutError("Controlled research cutoff")

        async def review_evidence(self, query, bundle, budget):
            self.calls["review"] += 1
            budget.reserve(model=1)
            if review_fails:
                raise ProviderError("controlled_review_failed")
            return bundle

        async def synthesize(self, data, bundle, profile, icp, recommendation, meta, budget):
            self.calls["synthesis"] += 1
            budget.reserve(model=1)
            return build_limited_result(data, bundle, profile, icp, recommendation, meta, budget.usage, None)

    provider = ControlledProvider()
    monkeypatch.setattr(workflow_module, "Provider", lambda settings: provider)
    monkeypatch.setattr(workflow_module, "FixtureProvider", lambda settings: provider)
    final = None
    for _ in range(4):
        job = c.repo.claim_job("timeout-worker-" + str(uuid4()))
        assert job and job["run_id"] == created["run_id"]
        asyncio.run(Workflow(c.settings, c.repo, Storage(c.settings)).process(job))
        final = c.repo.get_run(c.actors[0], created["run_id"])
        if final["status"] != "needs_clarification":
            break
        c.repo.answer(
            c.actors[0],
            created["run_id"],
            final["version"],
            {q["question_id"]: {"action": "leave_unknown"} for q in final["clarifications"]},
        )
    if stop in ("global_budget", "global_timeout"):
        assert final["status"] == "failed" and final["error"] == "BUDGET_EXHAUSTED"
        assert not final.get("briefing_id")
        assert provider.calls == {"assessment": 1, "research": 1, "review": 0, "synthesis": 0}
        return
    assert final["status"] == "partial" and final["briefing_id"]
    result = c.repo.get_briefing(c.actors[0], final["briefing_id"])
    assert any("Recherchezeitfenster" in note for note in result["limits"])
    assert provider.calls == {"assessment": 1, "research": 1, "review": 1, "synthesis": 1}
    assert result["usage"]["model_requests"] == 3 and result["usage"]["search_calls"] == 4
    assert 389.4 <= result["usage"]["active_seconds"] < 480
    assert "UNCOMMITTED_NEXT_WAVE" not in str(result)
    if stop == "raw_pending":
        pending = next(s for s in result["sources"] if s["source_id"] == "pending-source")
        assert not pending["verified"] and not pending["excerpt"] and pending["limitation"]
        assert "UNCHECKED_PENDING_SOURCE_TEXT" not in str(result)
    if has_complete and stop != "wrong_query":
        saved = next(f for f in result["facts"] if f["fact_id"] == "complete-business")
        assert saved["evidence_ids"] == ["s1"]
        assert saved["status"] == ("unknown" if review_fails else "verified")
    else:
        assert not result["facts"] and all(claim["kind"] == "unknown" for claim in result["company"])
    if review_fails:
        assert all(f["status"] == "unknown" for f in result["facts"])
        assert all(claim["kind"] == "unknown" for claim in result["company"])
    with c.repo.connection(worker=True) as db:
        row = db.execute("select checkpoint from atp.jobs where run_id=%s", (created["run_id"],)).fetchone()
        saved_result = db.execute(
            "select pdf_path from atp.briefings where id=%s", (final["briefing_id"],)
        ).fetchone()
    assert row["checkpoint"]["evidence_reviewed"] and row["checkpoint"]["research"]
    assert saved_result["pdf_path"]
    assert Storage(c.settings).get_pdf(saved_result["pdf_path"]).startswith(b"%PDF-")
