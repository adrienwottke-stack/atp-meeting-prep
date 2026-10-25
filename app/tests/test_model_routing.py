"""Research/review have independent explicit model routes; no external calls."""

from types import SimpleNamespace

import pytest
from test_providers import Response, extraction_fact, meeting, source

from atp.budget import Budget
from atp.config import Settings
from atp.models import EvidenceVerdict, Fact, ResearchBundle, ResearchQuery
from atp.providers import Extraction, Provider, ReviewedEvidence


@pytest.fixture
def configured(monkeypatch):
    for name in ("MODEL_RESEARCH", "MODEL_REVIEW", "MODEL_EXTRACTION", "MODEL_SYNTHESIS"):
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None, openai_api_key="controlled-test-value")


def test_sol_defaults_for_search_and_review_keep_luna_extraction_synthesis(configured):
    assert configured.model_research == configured.model_review == "gpt-6.1-sol"
    assert configured.model_extraction == configured.model_synthesis == "gpt-6-luna"


def test_explicit_model_overrides_remain_independent_and_read_only(configured, tmp_path):
    path = tmp_path / "models.env"
    path.write_text("MODEL_RESEARCH=research-override\nMODEL_REVIEW=review-override\n", encoding="utf-8")
    before = path.read_bytes()
    settings = Settings(_env_file=path)
    assert settings.model_research == "research-override" and settings.model_review == "review-override"
    assert settings.model_extraction == settings.model_synthesis == "gpt-6-luna"
    assert path.read_bytes() == before


async def test_public_research_routes_search_to_sol_and_extraction_to_luna(configured):
    calls = []

    class Responses:
        async def create(self, *, max_tool_calls=None, **kwargs):
            calls.append(("search", kwargs))
            return Response()

        async def parse(self, **kwargs):
            calls.append(("extract", kwargs))
            return SimpleNamespace(
                status="completed",
                usage=None,
                output_parsed=Extraction(facts=[extraction_fact()], ambiguous_participant_ids=[], gaps=[]),
            )

    class Fetcher:
        async def fetch(self, url, budget):
            budget.reserve(evidence=1)
            return source()

    provider = Provider(configured, client=SimpleNamespace(responses=Responses()), fetcher=Fetcher())
    bundle = await provider.research(ResearchQuery.from_input(meeting()), Budget(configured))
    assert bundle.facts
    assert any(kind == "search" for kind, _ in calls) and any(kind == "extract" for kind, _ in calls)
    assert all(call["model"] == "gpt-6.1-sol" for kind, call in calls if kind == "search")
    assert all(call["model"] == "gpt-6-luna" for kind, call in calls if kind == "extract")


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "explicit-review-override"])
async def test_content_review_uses_review_route_without_search_tools(configured, model):
    configured.model_review = model
    calls = []

    class Responses:
        async def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                status="completed",
                usage=None,
                output_parsed=ReviewedEvidence(
                    verdicts=[
                        EvidenceVerdict(
                            fact_id="headcount", verdict="supported", reason="Exact quoted company count."
                        )
                    ]
                ),
            )

    bundle = ResearchBundle(
        sources=[source()],
        facts=[
            Fact(
                fact_id="headcount",
                subject_ref="company",
                field="employees_total",
                value=120,
                evidence_ids=["s1"],
                status="verified",
                provenance="web_verified",
            )
        ],
    )
    reviewed = await Provider(configured, client=SimpleNamespace(responses=Responses())).review_evidence(
        ResearchQuery.from_input(meeting()), bundle, Budget(configured)
    )
    assert reviewed.facts[0].status == "verified"
    assert calls[0]["model"] == model
    assert calls[0]["tools"] == [] and calls[0]["tool_choice"] == "none"
