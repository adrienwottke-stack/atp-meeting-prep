"""Restarts and failed searches must preserve useful public research work."""

import asyncio
import copy

import pytest
from test_providers import meeting
from test_repository import database_case as database_case
from test_research_expansion import WaveProvider, real_settings

from atp.budget import Budget, LeaseLost
from atp.models import ResearchQuery
from atp.providers import ProviderError
from atp.research_pipeline import public_search_plan


@pytest.mark.parametrize("after_fetch", [False, True])
async def test_mid_wave_restart_reuses_search_and_each_saved_source(after_fetch):
    provider, states = WaveProvider(), []
    query = ResearchQuery.from_input(meeting(people=[]))

    def interrupt(state):
        states.append(copy.deepcopy(state))
        if bool(state["sources"]) == after_fetch:
            raise RuntimeError("worker stopped")

    with pytest.raises(RuntimeError, match="worker stopped"):
        await provider.research(query, Budget(real_settings()), persist=interrupt)
    saved = states[-1]
    assert saved["completed_waves"] == 0 and saved["active_wave"]
    assert len(provider.events) == 1
    result = await provider.research(query, Budget(real_settings(), saved["usage"]), checkpoint=saved)
    assert len(provider.events) == 3 and result.facts
    assert {f.field for f in result.facts} == {"business_model", "services", "business_development"}
    assert len(result.sources) == 3
    # Four pages total: root + one result per wave, with no duplicate charges.
    assert result and saved["usage"]["evidence_fetches"] == int(after_fetch)


async def test_failed_search_still_extracts_confirmed_company_website():
    class FailedSearch(WaveProvider):
        async def _request(self, budget, **kwargs):
            budget.reserve(model=1, searches=4)
            self.events.append(kwargs["data"])
            raise ProviderError("provider_request_failed")

    provider, budget = FailedSearch(), Budget(real_settings())
    result = await provider.research(ResearchQuery.from_input(meeting(people=[])), budget)
    assert any(f.field == "business_model" for f in result.facts)
    assert result.sources[0].url == "https://musterwerk.example/"
    assert any("Websuche begrenzt" in limit for limit in result.limits)
    assert budget.usage.evidence_fetches == 1


async def test_invalid_multi_source_extraction_retries_one_source_without_repeating_search():
    class InvalidBatch(WaveProvider):
        batches = []

        async def _extract_sources(self, query, sources, budget):
            self.batches.append(len(sources))
            if len(sources) > 1:
                budget.reserve(model=1)
                raise ProviderError("provider_schema_invalid")
            return await super()._extract_sources(query, sources, budget)

    provider, budget = InvalidBatch(), Budget(real_settings())
    result = await provider.research(ResearchQuery.from_input(meeting(people=[])), budget)
    assert provider.batches[:2] == [2, 1] and len(provider.events) == 3
    assert any(f.field == "business_model" for f in result.facts)
    assert budget.usage.model_requests == 9
    assert any("Einzelquelle" in limit for limit in result.limits)


async def test_company_source_is_extracted_separately_when_people_response_omits_business():
    from atp.providers import Extraction

    class PeopleFirst(WaveProvider):
        extraction_people = []

        async def _extract_sources(self, query, sources, budget):
            self.extraction_people.append(len(query.people))
            if query.people:
                budget.reserve(model=1)
                return Extraction(facts=[], ambiguous_participant_ids=[], gaps=[])
            return await super()._extract_sources(query, sources, budget)

    provider, budget = PeopleFirst(), Budget(real_settings())
    result = await provider.research(ResearchQuery.from_input(meeting()), budget)
    assert provider.extraction_people[:2] == [2, 0]
    assert any(f.field == "business_model" for f in result.facts)
    assert len(provider.events) == 3 and budget.usage.search_calls == 12


async def test_cancelled_fetch_keeps_completed_pages_and_retries_only_pending_page():
    started, states = asyncio.Event(), []

    class InterruptedFetch(WaveProvider):
        interrupted = False
        fetched = []

        async def fetch(self, url, budget):
            self.fetched.append(url)
            if not url.endswith("/") and not self.interrupted:
                self.interrupted = True
                budget.reserve(evidence=1)
                started.set()
                await asyncio.Event().wait()
            return await super().fetch(url, budget)

    provider, query = InterruptedFetch(), ResearchQuery.from_input(meeting(people=[]))
    task = asyncio.create_task(provider.research(
        query, Budget(real_settings()), persist=lambda state: states.append(copy.deepcopy(state))
    ))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = states[-1]
    assert saved["sources"] and saved["active_wave"]["queue"]
    budget = Budget(real_settings(), saved["usage"])
    result = await provider.research(query, budget, checkpoint=saved)
    assert result.facts and len(provider.events) == 3
    assert provider.fetched.count("https://musterwerk.example/") == 1
    assert provider.fetched.count("https://press.example.org/wave-1") == 2
    assert budget.usage.evidence_fetches == 5  # includes the cancelled attempt


async def test_rejected_incremental_checkpoint_stops_before_fetching():
    provider, budget = WaveProvider(), Budget(real_settings())
    with pytest.raises(LeaseLost):
        await provider.research(
            ResearchQuery.from_input(meeting(people=[])), budget, persist=lambda _: False
        )
    assert len(provider.events) == 1 and budget.usage.evidence_fetches == 0


def test_company_followup_queries_address_actual_missing_areas():
    plan = public_search_plan(
        ResearchQuery.from_input(meeting(people=[])), missing_areas=["developments", "icp"]
    )
    assert len(plan) <= 4 and len(set(plan)) == len(plan)
    assert any("site:musterwerk.example" in item and "Projekte" in item for item in plan)
    assert any("Mitarbeiter" in item and '"Musterwerk GmbH"' in item for item in plan)


async def test_quota_failure_keeps_discoveries_but_never_uses_direct_fallback():
    class NoQuota(WaveProvider):
        async def _request(self, budget, **kwargs):
            budget.reserve(model=1, searches=4)
            budget.usage.errors.append("provider_insufficient_quota")
            raise ProviderError("provider_insufficient_quota")

    budget = Budget(real_settings())
    result = await NoQuota().research(ResearchQuery.from_input(meeting(people=[])), budget)
    assert not result.facts and budget.usage.evidence_fetches == 0


async def test_closed_research_window_retains_pending_urls_without_unchecked_claims():
    provider, saved = WaveProvider(), []
    query = ResearchQuery.from_input(meeting(people=[]))

    def interrupt(state):
        if state["sources"]:
            saved.append(copy.deepcopy(state))
            raise RuntimeError("worker stopped")

    with pytest.raises(RuntimeError):
        await provider.research(query, Budget(real_settings()), persist=interrupt)
    usage = {**saved[0]["usage"], "active_seconds": 391}
    result = await provider.research(query, Budget(real_settings(), usage), checkpoint=saved[0])
    assert len(provider.events) == 1 and not result.facts
    assert result.sources[0].url == "https://musterwerk.example/"
    assert not result.sources[0].verified and not result.sources[0].excerpt
    assert result.sources[0].limitation


async def test_real_job_lease_restart_finishes_from_incremental_sources_to_pdf(database_case):
    from uuid import uuid4

    from test_repository import create

    from atp.models import InputAssessment, MeetingInput
    from atp.providers import build_limited_result
    from atp.storage import Storage
    from atp.workflow import Workflow

    c = database_case
    c.settings.worker_queue = "durable-research-" + str(uuid4())
    c.settings.output_dir = c.tmp_path / "pdfs"
    c.settings.storage_backend = "local"
    c.data.update(company={"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
                  people=[{"name": "Lea Sommer", "participant_id": "p1"}],
                  goal="Geschäftliche Abläufe besprechen")
    created = create(c)
    job = c.repo.claim_job("interrupted-worker")
    checkpoint = {"input_assessment": InputAssessment(evaluated=True).model_dump(mode="json")}

    class ResumableProvider(WaveProvider):
        async def review_evidence(self, query, bundle, budget):
            return bundle

        async def synthesize(self, data, bundle, profile, icp, recommendation, meta, budget):
            return build_limited_result(data, bundle, profile, icp, recommendation, meta, budget.usage, None)

    provider = ResumableProvider()

    def persist_usage(usage):
        return c.repo.checkpoint(job["id"], job["lease_token"], "researching", checkpoint, usage)

    def persist_progress(state):
        checkpoint["research_progress"] = copy.deepcopy(state)
        assert persist_usage(state["usage"])
        if state["sources"]:
            raise RuntimeError("worker process interrupted")

    with pytest.raises(RuntimeError, match="worker process interrupted"):
        await provider.research(
            ResearchQuery.from_input(MeetingInput.model_validate(c.data)),
            Budget(c.settings, persist=persist_usage), persist=persist_progress,
        )
    c.admin.execute("update atp.jobs set lease_until=now()-interval '1 second' where id=%s", (job["id"],))
    resumed = c.repo.claim_job("replacement-worker")
    assert resumed and resumed["run_id"] == created["run_id"]
    assert resumed["checkpoint"]["research_progress"]["sources"]
    workflow = Workflow(c.settings, c.repo, Storage(c.settings))
    workflow.provider = provider
    await workflow.process(resumed)
    final = c.repo.get_run(c.actors[0], created["run_id"])
    for _ in range(3):
        if final["status"] != "needs_clarification":
            break
        c.repo.answer(c.actors[0], created["run_id"], final["version"], {
            question["question_id"]: {"action": "leave_unknown"} for question in final["clarifications"]
        })
        await workflow.process(c.repo.claim_job("replacement-worker"))
        final = c.repo.get_run(c.actors[0], created["run_id"])
    assert final["status"] == "partial" and final["briefing_id"]
    result = c.repo.get_briefing(c.actors[0], final["briefing_id"])
    assert any(f["field"] == "business_model" for f in result["facts"])
    assert len(provider.events) == 3 and result["usage"]["evidence_fetches"] == 4
    with c.repo.connection(worker=True) as db:
        saved = db.execute("select pdf_path from atp.briefings where id=%s", (final["briefing_id"],)).fetchone()
    assert Storage(c.settings).get_pdf(saved["pdf_path"]).startswith(b"%PDF-")
