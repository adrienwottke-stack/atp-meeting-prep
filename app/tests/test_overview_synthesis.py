from types import SimpleNamespace

import pytest
from test_pdf import sample_result
from test_providers import Budget, settings

from atp.models import ClarificationPlan, Fact, OverviewItem, ResearchBundle
from atp.providers import Narrative, Provider, ProviderError
from atp.workflow import decorate_result


async def test_german_authored_overview_keeps_checked_references_and_excludes_unknown_facts():
    base = sample_result()
    bundle = ResearchBundle(sources=base.sources, facts=base.facts)
    bundle.facts.append(Fact(field="business_model", value="Unsupported premise", status="unknown"))
    fact = base.facts[0]
    authored = OverviewItem(
        category="company",
        text="Das Unternehmen fertigt industrielle Komponenten.",
        kind="fact",
        fact_ids=[fact.fact_id],
        evidence_ids=fact.evidence_ids,
    )
    captured = []

    class Controlled(Provider):
        async def _request(self, budget, **kwargs):
            captured.append(kwargs["data"])
            return Narrative(
                questions=base.questions, storyline=base.storyline, opportunities=[], overview=[authored]
            )

    provider = Controlled(settings(), client=SimpleNamespace())
    from atp.profiles import load_profile

    result = await provider.synthesize(
        base.input,
        bundle,
        load_profile(),
        base.icp,
        base.recommendation,
        {"meeting_id": "meeting", "run_id": "run", "result_version": 1},
        Budget(),
    )
    assert all(f["status"] != "unknown" for f in captured[0]["verified_facts"])
    decorate_result(result, base.input, bundle, base.icp, base.recommendation, ClarificationPlan(), {})
    assert result.overview[0].text == authored.text
    assert result.overview[0].fact_ids == [fact.fact_id]
    assert base.recommendation.value in result.overview[-1].text


async def test_authored_overview_cannot_reference_invented_fact():
    base = sample_result()

    class Controlled(Provider):
        async def _request(self, budget, **kwargs):
            return Narrative(
                questions=base.questions,
                storyline=base.storyline,
                opportunities=[],
                overview=[
                    OverviewItem(category="company", text="Invented", kind="fact", fact_ids=["invented"])
                ],
            )

    from atp.profiles import load_profile

    with pytest.raises(ProviderError, match="synthesis_validation_failed"):
        await Controlled(settings(), client=SimpleNamespace()).synthesize(
            base.input,
            ResearchBundle(sources=base.sources, facts=base.facts),
            load_profile(),
            base.icp,
            base.recommendation,
            {"meeting_id": "meeting", "run_id": "run", "result_version": 1},
            Budget(),
        )
