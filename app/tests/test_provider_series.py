"""The public evaluation harness has no DB/auth side effects or fixture fallback."""

import importlib.util
from pathlib import Path

from atp.models import InputAssessment, ResearchBundle, ResearchQuery
from atp.providers import ProviderError

SPEC = importlib.util.spec_from_file_location(
    "evaluate_provider_series", Path(__file__).parents[1] / "scripts/evaluate_provider_series.py"
)
series = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(series)


def test_config_is_read_only_real_and_hard_bounded(tmp_path):
    config = tmp_path / "evaluation.env"
    config.write_text("OPENAI_API_KEY=controlled-test-value\nFIXTURE_MODE=true\n", encoding="utf-8")
    original = config.read_bytes()
    settings = series.evaluation_settings(config)
    assert settings.fixture_mode is False
    assert (settings.deadline_seconds, settings.research_cutoff_seconds) == (480, 390)
    assert (settings.max_model_requests, settings.max_search_calls, settings.max_evidence_fetches) == (
        16,
        24,
        40,
    )
    assert config.read_bytes() == original


def test_five_public_cases_have_three_schaeffler_two_trumpf_no_role_assertions():
    cases = series.public_cases()
    assert [c["company"]["name"] for c in cases].count("Schaeffler") == 3
    assert [c["company"]["name"] for c in cases].count("TRUMPF SE + Co. KG") == 2
    assert all(not p.get("role_hint") and not p.get("profile_url") for c in cases for p in c["people"])


async def test_synthesis_failure_generates_honest_public_only_partial_artifacts(tmp_path):
    config = tmp_path / "evaluation.env"
    config.write_text("OPENAI_API_KEY=controlled-test-value\n", encoding="utf-8")

    class Controlled:
        def __init__(self, settings, events, callback):
            pass

        async def assess_input(self, data, budget):
            assert not data.internal_context
            return InputAssessment(evaluated=True)

        async def research(self, query, budget, **kwargs):
            assert isinstance(query, ResearchQuery)
            assert set(query.model_dump()) == {"company_name", "domain", "people"}
            return ResearchBundle(gaps=["No controlled source available"])

        async def review_evidence(self, query, bundle, budget):
            return bundle

        async def synthesize(self, *args):
            raise ProviderError("controlled_synthesis_failure")

    result = await series.evaluate_case(
        series.evaluation_settings(config), series.public_cases()[0], 1, tmp_path, provider_factory=Controlled
    )
    assert result["status"] == "partial" and result["synthesis_error"] == "controlled_synthesis_failure"
    # Missing public research is a documented limitation, not user homework.
    assert result["quality"]["state"] == "limited"
    assert not result["facts"] and result["gaps"]
    assert result["scope"] == "provider_pipeline_only" and not result["fixture_mode"]
    assert (tmp_path / "briefing-1.json").is_file() and (tmp_path / "run-1.json").is_file()
    assert all("controlled-test-value" not in p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
