"""Regressions from the public-data acceptance failures, with no external calls."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_providers import Budget, Response, Responses, extraction_fact, meeting, settings, source

from atp.evidence import EvidenceError, company_links
from atp.models import ResearchBundle, ResearchQuery, Source
from atp.providers import (
    Extraction,
    Provider,
    ProviderError,
    _fetch_host,
    _fetch_key,
    _rank_urls,
    extraction_schema,
    validate_extraction,
)


def test_fetch_identity_ignores_only_tracking_and_keeps_unrelated_hosts_separate():
    assert (
        _fetch_key("https://example.org/report?id=1&utm_source=openai#section")
        == "https://example.org/report?id=1"
    )
    assert _fetch_key("https://example.org/report?id=1") != _fetch_key("https://example.org/report?id=2")
    assert _fetch_host("https://de.linkedin.com/in/person") == "linkedin.com"
    assert _fetch_host("https://linkedin.com.example.org/page") == "linkedin.com.example.org"


def test_subject_contract_is_constrained_and_exact_name_alias_preserves_guards():
    query = ResearchQuery.from_input(meeting())
    schema = extraction_schema(query, [source()])
    valid = {"facts": [extraction_fact().model_dump()], "ambiguous_participant_ids": [], "gaps": []}
    schema.model_validate(valid)
    valid["facts"][0]["subject_ref"] = "unrelated-company"
    with pytest.raises(ValidationError):
        schema.model_validate(valid)
    assert validate_extraction(Extraction.model_validate(valid), [source()], query).gaps
    valid["facts"][0]["subject_ref"] = "Musterwerk GmbH"
    assert validate_extraction(Extraction.model_validate(valid), [source()], query).facts[0].value == 120
    valid["facts"][0]["value_json"] = "9000"
    assert not validate_extraction(Extraction.model_validate(valid), [source()], query).facts


@pytest.mark.parametrize("last_status,should_fail", [("searching", False), ("completed", True), (None, True)])
async def test_search_cap_distinguishes_processed_calls_from_pending_attempt(last_status, should_fail):
    class CapResponse(Response):
        def model_dump(self):
            return {
                "output": [
                    {"type": "web_search_call", "status": status, "action": {"type": "search"}}
                    for status in ["completed"] * 4 + [last_status]
                ]
            }

    budget = Budget()
    provider = Provider(settings(), client=SimpleNamespace(responses=Responses(CapResponse())))
    if should_fail:
        with pytest.raises(ProviderError, match="search_cap_not_confirmed"):
            await provider._request(budget, model="test", instructions="public", data={}, search=True)
    else:
        await provider._request(budget, model="test", instructions="public", data={}, search=True)
    assert len(budget.usage.search_trace[0]["items"]) == 5
    assert budget.usage.observed_search_calls == (5 if last_status == "completed" else 4)
    assert budget.usage.search_calls == 4


def test_company_link_discovery_and_ranking_do_not_invent_paths_or_accept_foreign_hosts():
    html = b"""<a href="/company?tracking=1">About</a><a href="https://evil.test/impressum">x</a>
    <a href="http://[">malformed</a>
    <a href="http://127.0.0.1/company">x</a><a href="/products.pdf">x</a>
    <a href="https://blog.example.com/company/story">story</a>"""
    assert company_links(html, "https://www.example.com/") == [
        "https://www.example.com/company",
        "https://blog.example.com/company/story",
    ]
    assert _rank_urls(
        [
            "http://[",
            "https://example.com/catalog.pdf",
            "https://example.com/product",
            "https://example.com/company",
        ],
        "www.example.com",
    ) == [
        "https://example.com/company",
        "https://example.com/product",
    ]


async def test_blocked_origin_is_not_retried_and_independent_search_has_no_internal_context():
    calls, fetched = [], []

    class Fetcher:
        async def fetch(self, url, budget):
            fetched.append(url)
            budget.reserve(evidence=1)
            if "musterwerk.example" in url:
                raise EvidenceError("evidence_http_403")
            return Source(
                url=url,
                source_id="independent",
                title="Public report",
                excerpt="Musterwerk GmbH beschäftigt 120 Mitarbeitende im Jahr 2026.",
                verified=True,
                discovered_urls=["https://press.example.org/company/unrelated"],
            )

    class FallbackProvider(Provider):
        async def _request(self, budget, **kwargs):
            calls.append(kwargs)
            budget.reserve(model=1, searches=4)
            url = (
                "https://musterwerk.example/company" if len(calls) == 1 else "https://press.example.org/news"
            )
            return {
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"type": "search", "sources": [{"url": url}]},
                    }
                ]
            }

        async def _extract_sources(self, query, sources, budget):
            return Extraction(
                facts=[extraction_fact(source_id="independent")], ambiguous_participant_ids=[], gaps=[]
            )

    p = FallbackProvider(settings(), client=SimpleNamespace(), fetcher=Fetcher())
    bundle = await p.research(ResearchQuery.from_input(meeting()), Budget())
    assert len(calls) == 2
    assert sum("musterwerk.example" in url for url in fetched) == 1
    assert "TEST-INTERN-4711" not in json.dumps(calls)
    assert bundle.facts[0].value == 120
    assert not any("unrelated" in url for url in fetched)
    assert any("403" in limit for limit in bundle.limits)


async def test_linkedin_999_blocks_regional_hosts_and_fallback_preserves_independent_sources():
    fetched = []

    class Fetcher:
        async def fetch(self, url, budget):
            fetched.append(url)
            budget.reserve(evidence=1)
            if "linkedin.com" in url:
                raise EvidenceError("evidence_http_999")
            return source()

    class SearchProvider(Provider):
        async def _request(self, budget, **kwargs):
            return {
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {
                            "sources": [
                                {"url": u}
                                for u in [
                                    "https://de.linkedin.com/in/lea?utm_source=openai",
                                    "https://de.linkedin.com/in/lea",
                                    "https://www.linkedin.com/in/another",
                                    "https://musterwerk.example/?utm_source=openai",
                                    "https://press.example.org/news",
                                ]
                            ],
                        },
                    }
                ]
            }

        async def _extract_sources(self, query, sources, budget):
            return Extraction(facts=[extraction_fact()], ambiguous_participant_ids=[], gaps=[])

    provider = SearchProvider(settings(), client=SimpleNamespace(), fetcher=Fetcher())
    bundle = await provider.research(ResearchQuery.from_input(meeting()), Budget())
    assert sum("linkedin.com" in url for url in fetched) == 1
    assert sum("musterwerk.example" in url for url in fetched) == 1
    assert "https://press.example.org/news" in fetched
    assert bundle.facts
    assert any("999" in note for note in bundle.limits)


def test_missing_person_does_not_block_independent_company_result():
    bundle = validate_extraction(
        Extraction(
            facts=[extraction_fact()], ambiguous_participant_ids=["p2"], gaps=["Keine Personenquelle"]
        ),
        [source()],
        ResearchQuery.from_input(meeting()),
    )
    assert isinstance(bundle, ResearchBundle) and bundle.facts and not bundle.clarifications


def test_legal_form_punctuation_does_not_discard_independently_supported_company_fact():
    query = ResearchQuery(company_name="TRUMPF SE + Co. KG", domain="www.trumpf.com")
    quote = "Das Unternehmen TRUMPF entwickelt Maschinenbau-Lösungen für die Industrie."
    s = Source(
        source_id="s1", url="https://press.example.org/report", title="TRUMPF", excerpt=quote, verified=True
    )
    candidate = extraction_fact(
        field="industry", value_json='"Maschinenbau"', quote=quote, information_as_of=None
    )
    extraction = Extraction(facts=[candidate], ambiguous_participant_ids=[], gaps=[])
    assert validate_extraction(extraction, [s], query).facts[0].value == "Maschinenbau"
    query.company_name = "Unrelated SE + Co. KG"
    assert not validate_extraction(extraction, [s], query).facts
