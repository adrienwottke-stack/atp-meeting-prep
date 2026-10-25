from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError

from atp.models import MeetingInput, ResearchBundle, ResearchQuery, Source, Usage
from atp.profiles import load_profile
from atp.providers import (
    ExtractedFact,
    Extraction,
    FixtureProvider,
    Narrative,
    Provider,
    ProviderError,
    _search_urls,
    apply_research_answers,
    build_limited_result,
    validate_extraction,
)
from atp.rules import evaluate_icp, recommend


class Budget:
    def __init__(self):
        self.usage = Usage()
        self.remaining_seconds = 300

    def reserve(self, model=0, searches=0, evidence=0):
        self.usage.model_requests += model
        self.usage.search_calls += searches
        self.usage.evidence_fetches += evidence
        if self.usage.model_requests > 8 or self.usage.search_calls > 12 or self.usage.evidence_fetches > 20:
            raise RuntimeError("Budget exhausted")

    def timeout(self, maximum):
        return min(maximum, self.remaining_seconds)


def settings(**kwargs):
    return SimpleNamespace(
        openai_api_key="test-only",
        fixture_mode=False,
        model_extraction="extraction-model",
        model_research="research-model",
        model_synthesis="synthesis-model",
        **kwargs,
    )


def meeting(**kwargs):
    value = {
        "company": {"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
        "people": [
            {"participant_id": "p1", "name": "Lea Sommer"},
            {"participant_id": "p2", "name": "Tobias Kern"},
        ],
        "meeting_type": "setup",
        "relationship": "new_contact",
        "goal": "Nächsten Schritt klären",
        "confirmed": True,
        "public_identifiers_confirmed": True,
        "internal_context": "TEST-INTERN-4711",
    }
    value.update(kwargs)
    return MeetingInput.model_validate(value)


def meta(**kwargs):
    return {"meeting_id": "meeting", "run_id": "run", "result_version": 1, "fixture_mode": False, **kwargs}


class Response:
    status = "completed"
    max_tool_calls = 4
    usage = SimpleNamespace(input_tokens=10, output_tokens=20)
    output_parsed = None

    def model_dump(self):
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {"type": "search", "sources": [{"url": "https://example.com/a"}]},
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "text": "Untrusted https://invented.invalid/",
                            "annotations": [{"type": "url_citation", "url": "https://example.com/a"}],
                        }
                    ],
                },
            ]
        }


class Responses:
    def __init__(self, response=None):
        self.calls, self.response = [], response or Response()

    async def create(self, *, max_tool_calls=None, **kwargs):
        self.calls.append({**kwargs, "max_tool_calls": max_tool_calls})
        return self.response

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_search_only_uses_actual_provider_citation_metadata():
    assert _search_urls(Response()) == ["https://example.com/a"]
    assert (
        _search_urls(
            {
                "output": [
                    {"type": "web_search_call", "action": {"type": "search", "sources": None}},
                    {"type": "reasoning", "content": None},
                ]
            }
        )
        == []
    )


async def test_t13_internal_context_never_reaches_search_and_separate_synthesis_tools():
    data, budget, responses = meeting(), Budget(), Responses()
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    query = ResearchQuery.from_input(data)
    await provider._request(
        budget, model="search", instructions="public", data=query.model_dump(), search=True
    )
    search_call = responses.calls[-1]
    assert "TEST-INTERN-4711" not in search_call["input"]
    assert search_call["tools"] == [{"type": "web_search"}]
    assert search_call["max_tool_calls"] == 4 and not search_call["store"]
    assert budget.usage.search_calls == 4 and budget.usage.model_requests == 1
    responses.response.output_parsed = SimpleNamespace(value="x")
    await provider._request(
        budget, model="synth", instructions="private", data=data.model_dump(mode="json"), schema=Narrative
    )
    assert "TEST-INTERN-4711" in responses.calls[-1]["input"]
    assert responses.calls[-1]["tools"] == [] and responses.calls[-1]["tool_choice"] == "none"
    assert budget.usage.input_tokens == 20 and budget.usage.output_tokens == 40


async def test_search_cap_fail_closed_if_sdk_or_response_cannot_confirm():
    async def unsupported(**kwargs):
        raise AssertionError("Should not call unsupported SDK")

    provider = Provider(settings(), client=SimpleNamespace(responses=SimpleNamespace(create=unsupported)))
    with pytest.raises(ProviderError, match="unsupported_sdk"):
        await provider._request(Budget(), model="m", instructions="x", data={}, search=True)
    response = Response()
    response.max_tool_calls = None
    provider = Provider(settings(), client=SimpleNamespace(responses=Responses(response)))
    with pytest.raises(ProviderError, match="not_confirmed"):
        await provider._request(Budget(), model="m", instructions="x", data={}, search=True)


async def test_retries_and_failures_count_full_reserved_search_budget(monkeypatch):
    class Failing(Responses):
        async def create(self, *, max_tool_calls=None, **kwargs):
            self.calls.append(kwargs)
            raise APIStatusError(
                "synthetic failure",
                response=httpx.Response(
                    503, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
                ),
                body=None,
            )

    async def no_wait(_):
        pass

    monkeypatch.setattr("atp.providers.asyncio.sleep", no_wait)
    responses, budget = Failing(), Budget()
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    with pytest.raises(ProviderError, match="provider_request_failed"):
        await provider._request(budget, model="m", instructions="x", data={}, search=True)
    assert len(responses.calls) == 3
    assert budget.usage.model_requests == 3 and budget.usage.search_calls == 12
    assert len(budget.usage.errors) == 3


async def test_400_does_not_retry_without_search_cap():
    class Failing(Responses):
        async def create(self, *, max_tool_calls=None, **kwargs):
            self.calls.append(kwargs)
            raise APIStatusError(
                "unsupported",
                response=httpx.Response(
                    400, request=httpx.Request("POST", "https://api.openai.com/v1/responses")
                ),
                body=None,
            )

    responses, budget = Failing(), Budget()
    with pytest.raises(ProviderError, match="search_cap_or_model_rejected"):
        await Provider(settings(), client=SimpleNamespace(responses=responses))._request(
            budget, model="m", instructions="x", data={}, search=True
        )
    assert len(responses.calls) == 1 and budget.usage.search_calls == 4


def extraction_fact(**kwargs):
    data = {
        "subject_ref": "company",
        "field": "employees_total",
        "value_json": "120",
        "source_id": "s1",
        "quote": "Musterwerk GmbH beschäftigt 120 Mitarbeitende im Jahr 2026.",
        "information_as_of": "2026",
        "historical": False,
        "identity_company": None,
        "identity_additional": None,
    }
    data.update(kwargs)
    return ExtractedFact(**data)


def source(excerpt="Musterwerk GmbH beschäftigt 120 Mitarbeitende im Jahr 2026."):
    return Source(
        source_id="s1", url="https://musterwerk.example/about", title="Quelle", excerpt=excerpt, verified=True
    )


def extract(candidates, sources=None, ambiguous=None):
    return validate_extraction(
        Extraction(facts=candidates, ambiguous_participant_ids=ambiguous or [], gaps=[]),
        sources or [source()],
        ResearchQuery.from_input(meeting()),
    )


def test_r05_value_and_concrete_quote_required_snippet_or_random_url_insufficient():
    assert extract([extraction_fact()]).facts[0].value == 120
    assert not extract([extraction_fact(value_json="9000")]).facts
    assert not extract([extraction_fact(quote="A fabricated source contains 120 employees.")]).facts
    assert not extract([extraction_fact(source_id="unknown")]).facts
    assert not extract([extraction_fact()], [source().model_copy(update={"verified": False})]).facts


def test_parent_company_and_general_role_are_not_ownership_proof():
    quote = "Musterwerk GmbH hat Geschäftsführerin Lea Sommer und 120 Mitarbeitende."
    assert not extract(
        [extraction_fact(field="owner_or_family_led", value_json="true", quote=quote)], [source(quote)]
    ).facts
    other = Source(
        source_id="s1",
        url="https://different.com/",
        title="Konzern",
        excerpt="Der Großkonzern beschäftigt 120 Mitarbeitende.",
        verified=True,
    )
    assert not extract([extraction_fact(quote=other.excerpt)], [other]).facts


def test_t11_historical_roles_and_undated_currency_are_distinct():
    quote = "Lea Sommer war 2018 Geschäftsführerin bei Musterwerk GmbH."
    candidate = extraction_fact(
        subject_ref="p1",
        field="current_role",
        value_json='"Geschäftsführerin"',
        quote=quote,
        information_as_of="2018",
        identity_company="Musterwerk GmbH",
        identity_additional="Geschäftsführerin",
    )
    bundle = extract([candidate], [source(quote)])
    assert bundle.facts[0].status == "historical" and bundle.facts[0].field == "historical_role"
    quote = "Lea Sommer ist Geschäftsführerin bei Musterwerk GmbH."
    candidate.quote, candidate.information_as_of = quote, "2026"
    bundle = extract([candidate], [source(quote)])
    assert bundle.facts[0].information_as_of is None and bundle.facts[0].field == "undated_role"
    result_data, profile = meeting(), load_profile()
    icp = evaluate_icp(bundle.facts, result_data, profile)
    result = build_limited_result(
        result_data, bundle, profile, icp, recommend(result_data, icp), meta(), Usage(), None
    )
    assert "Aktualität ungeklärt" in result.people[0].role.text
    assert not result.people[0].career
    quote = "Lea Sommer ist 2026 Geschäftsführerin bei Musterwerk GmbH."
    candidate.quote = quote
    assert extract([candidate], [source(quote)]).facts[0].status == "verified"
    candidate.identity_additional = None
    assert not extract([candidate], [source(quote)]).facts


def test_missing_person_evidence_does_not_manufacture_identity_conflict():
    bundle = extract([extraction_fact()], ambiguous=["p1"])
    assert bundle.facts and bundle.facts[0].subject_ref == "company"
    assert not bundle.clarifications
    assert any("Lea Sommer" in gap for gap in bundle.gaps)


def test_k03_explicit_candidate_selection_keeps_only_supported_identity():
    quote = "Lea Sommer ist 2026 Geschäftsführerin bei Musterwerk GmbH."
    candidate = extraction_fact(
        subject_ref="p1",
        field="current_role",
        value_json='"Geschäftsführerin"',
        quote=quote,
        information_as_of="2026",
        identity_company="Musterwerk GmbH",
        identity_additional="Geschäftsführerin",
    )
    bundle = extract([candidate], [source(quote)], ambiguous=["p1"])
    assert bundle.facts[0].status == "conflicting"
    option = next(o for o in bundle.clarifications[0].options if o.startswith("Quelle s1 bestätigen:"))
    answered, _data = apply_research_answers(bundle, meeting(), {"K03:p1": option})
    assert not answered.clarifications
    assert answered.facts[0].status == "verified" and answered.facts[0].evidence_ids == ["s1"]


def test_workforce_and_knowledge_workers_not_interchangeable_and_nonfinite_rejected():
    from atp.providers import _value_supported

    assert _value_supported("employees_total", 120, "Die Firma hat 120 Mitarbeitende und 40 Wissensarbeiter.")
    assert not _value_supported(
        "employees_total", 40, "Die Firma hat 120 Mitarbeitende und 40 Wissensarbeiter."
    )
    assert not _value_supported(
        "knowledge_workers", 120, "Die Firma hat 120 Mitarbeitende und 40 Wissensarbeiter."
    )
    assert not _value_supported("employees_total", float("inf"), "Viele Mitarbeitende.")
    assert not _value_supported("employees_total", float("nan"), "Viele Mitarbeitende.")


def test_no_inferred_negative_ownership_or_exclusions():
    from atp.providers import _value_supported

    assert not _value_supported("owner_or_family_led", False, "Das Unternehmen ist nicht familiengeführt.")
    assert not _value_supported("owner_or_family_led", False, "Das Unternehmen ist nicht eigentümergeführt.")
    assert _value_supported(
        "owner_or_family_led", False, "Das Unternehmen ist weder eigentümer- noch familiengeführt."
    )
    assert not _value_supported(
        "is_corporation", False, "This is an independent company within a holding structure."
    )
    assert not _value_supported(
        "is_public_authority", False, "Eine privatwirtschaftlich organisierte Stelle."
    )


def test_private_person_details_dropped_and_information_date_not_retrieval():
    quote = "Lea Sommer bei Musterwerk GmbH: Hobby Gesundheit und Privatleben im Jahr 2026."
    candidate = extraction_fact(
        subject_ref="p1",
        field="professional_topic",
        value_json='"Gesundheit"',
        quote=quote,
        identity_company="Musterwerk GmbH",
        identity_additional="Hobby",
    )
    assert not extract([candidate], [source(quote)]).facts
    candidate = extraction_fact(information_as_of="2025")
    assert extract([candidate]).facts[0].information_as_of is None


async def test_fixture_requires_explicit_activation_and_does_not_invent_unknown_case():
    with pytest.raises(ProviderError):
        FixtureProvider(settings())
    fixture_settings = settings()
    fixture_settings.fixture_mode = True
    provider = FixtureProvider(fixture_settings)
    query = ResearchQuery(company_name="Unknown GmbH", domain="unknown.example")
    bundle = await provider.research(query, Budget())
    assert not bundle.facts and bundle.limits


@pytest.mark.parametrize(
    "meeting_type", ["setup", "audit_consultation", "enablement_consultation", "project_review"]
)
async def test_t01_t04_f01_complete_six_sections_per_anlass(meeting_type):
    fixture_settings = settings()
    fixture_settings.fixture_mode = True
    provider, budget = FixtureProvider(fixture_settings), Budget()
    data = meeting(
        meeting_type=meeting_type,
        relationship="ongoing_project" if meeting_type == "project_review" else "new_contact",
    )
    profile = load_profile()
    bundle = await provider.research(ResearchQuery.from_input(data), budget)
    icp = evaluate_icp(bundle.facts, data, profile)
    result = await provider.synthesize(data, bundle, profile, icp, recommend(data, icp), meta(), budget)
    assert len(result.company) >= 1 and len(result.people) == 2
    assert (
        result.opportunities and len(result.questions) == 5 and len(result.storyline) == 4 and result.sources
    )
    assert result.fixture_mode and "Synthetischer" in result.limits[-1]
    assert result.profile_hash and result.icp.status == "matching"
    if meeting_type == "project_review":
        assert any("Fortschritt" in q.text for q in result.questions)


async def test_fixture_text_preview_and_missing_values_not_invented():
    s = settings()
    s.fixture_mode = True
    provider, budget = FixtureProvider(s), Budget()
    result = await provider.parse(
        "Musterwerk GmbH, musterwerk.example; Lea Sommer; Tobias Kern; Setup; Neukontakt", budget
    )
    assert result["preview"]["company"]["name"] == "Musterwerk GmbH"
    assert len(result["preview"]["people"]) == 2
    assert not result["preview"]["confirmed"]
    empty = await provider.parse("Bitte einen Termin vorbereiten", budget)
    assert empty["preview"]["company"] == {"name": "", "website": "", "entity_hint": ""}
    assert not empty["preview"]["people"] and empty["preview"]["meeting_type"] is None


def test_limited_live_result_has_no_fixture_facts_and_partial_reason():
    data, profile = meeting(), load_profile()
    bundle = ResearchBundle(gaps=["Quelle gesperrt"])
    icp = evaluate_icp([], data, profile)
    result = build_limited_result(
        data, bundle, profile, icp, recommend(data, icp), meta(), Usage(), "API-Zeitüberschreitung"
    )
    assert result.status == "partial" and not result.fixture_mode and not result.facts
    assert all(c.kind == "unknown" for c in result.company)


async def test_fetched_sources_preserved_on_failed_extraction():
    class Fetcher:
        async def fetch(self, url, budget):
            return source()

    provider = Provider(settings(), client=SimpleNamespace(responses=Responses()), fetcher=Fetcher())

    async def failed(*args):
        raise ProviderError("timeout")

    provider._extract_sources = failed
    bundle = await provider.research(ResearchQuery.from_input(meeting()), Budget())
    assert bundle.sources and not bundle.facts and bundle.limits
    assert all(not s.excerpt and not s.verified for s in bundle.sources)
