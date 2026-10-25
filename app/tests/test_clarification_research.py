"""Decision-relevant regressions, entirely controlled; no API or network calls."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from test_providers import Budget, Response, Responses, extraction_fact, meeting, settings, source

from atp.budget import BudgetExceeded
from atp.models import EvidenceVerdict, Fact, ResearchBundle, ResearchQuery, Source
from atp.providers import (
    AssessedInput,
    Extraction,
    FixtureProvider,
    InputIssue,
    Provider,
    ProviderError,
    ReviewedEvidence,
    _value_supported,
    apply_research_answers,
    validate_extraction,
)


def bundle_fact(**kwargs):
    f = Fact(
        fact_id="candidate-1",
        field="employees_total",
        value=120,
        evidence_ids=["s1"],
        status="verified",
        provenance="web_verified",
        **kwargs,
    )
    return ResearchBundle(sources=[source()], facts=[f])


@pytest.mark.parametrize(
    "quote",
    [
        "Lea Sommer ist 2026 nicht Geschäftsführerin bei Musterwerk GmbH.",
        "Lea Sommer is not CEO of Musterwerk GmbH in 2026.",
        "Lea Sommer ist 2026 keine Geschäftsführerin bei Musterwerk GmbH.",
    ],
)
def test_role_negation_never_passes_string_occurrence_guard(quote):
    role = "CEO" if "CEO" in quote else "Geschäftsführerin"
    assert not _value_supported("current_role", role, quote)


@pytest.mark.parametrize(
    "field,value,quote,expected",
    [
        ("owner_or_family_led", True, "Das Unternehmen ist in Familienbesitz.", False),
        ("owner_or_family_led", True, "The company is not family-led.", False),
        ("owner_or_family_led", False, "The company is not family-led.", False),
        ("owner_or_family_led", False, "Das Unternehmen ist weder eigentümer- noch familiengeführt.", True),
        ("owner_or_family_led", True, "Nicht familiengeführt, aber inhabergeführt.", True),
        ("is_corporation", True, "Das Unternehmen ist kein Konzern.", False),
        ("is_corporation", False, "Keinem Konzern zugehörig.", True),
        ("is_public_authority", True, "This company is not a public authority.", False),
        ("is_public_authority", False, "Information unavailable.", False),
    ],
)
def test_boolean_guards_preserve_unknown_and_operational_leadership(field, value, quote, expected):
    assert _value_supported(field, value, quote) is expected


def roles_bundle(compatible=False):
    today = date.today().isoformat()
    quote = (
        f"Lea Sommer ist am {today} Geschäftsführerin und Leiterin IT bei Musterwerk GmbH."
        if compatible
        else (
            f"Lea Sommer ist am {today} Geschäftsführerin bei Musterwerk GmbH. "
            f"Lea Sommer ist am {today} Leiterin IT bei Musterwerk GmbH."
        )
    )
    candidates = [
        extraction_fact(
            subject_ref="p1",
            field="current_role",
            value_json=json.dumps(role),
            quote=quote,
            information_as_of=today,
            identity_company="Musterwerk GmbH",
            identity_additional=role,
        )
        for role in ("Geschäftsführerin", "Leiterin IT")
    ]
    return validate_extraction(
        Extraction(facts=candidates, ambiguous_participant_ids=[], gaps=[]),
        [source(quote)],
        ResearchQuery.from_input(meeting()),
    )


def test_incompatible_roles_flagged_without_model_ambiguity_and_no_source_option():
    bundle = roles_bundle()
    assert {f.status for f in bundle.facts} == {"conflicting"}
    assert bundle.clarifications[0].question_id == "K03:p1"
    assert all(not o.startswith("Quelle ") for o in bundle.clarifications[0].options)


def test_explicit_dual_role_is_compatible_but_separate_assertions_are_not():
    compatible = roles_bundle(True)
    assert all(f.status == "verified" for f in compatible.facts)
    assert not compatible.clarifications
    assert roles_bundle().clarifications


def test_exact_candidate_selection_does_not_promote_other_facts_on_same_source():
    bundle = roles_bundle()
    chosen = bundle.facts[0].fact_id
    resolved, _ = apply_research_answers(bundle, meeting(), {"K03:p1": {"action": "select", "value": chosen}})
    assert len(resolved.facts) == 1 and resolved.facts[0].fact_id == chosen
    assert resolved.facts[0].status == "verified" and not resolved.clarifications
    assert len(bundle.facts) == 2  # caller checkpoint untouched


def test_semantically_rejected_candidate_cannot_be_promoted_by_user_selection():
    bundle = roles_bundle()
    chosen = bundle.facts[0].fact_id
    bundle.evidence_review = [
        EvidenceVerdict(fact_id=chosen, verdict="contradicted", reason="Wrong identity")
    ]
    resolved, _ = apply_research_answers(bundle, meeting(), {"K03:p1": {"action": "select", "value": chosen}})
    assert all(f.status == "conflicting" for f in resolved.facts)
    assert resolved.clarifications


def test_structured_leave_unknown_removes_only_affected_profile():
    bundle = roles_bundle()
    bundle.facts.append(Fact(subject_ref="company", field="industry", value="Maschinenbau", status="unknown"))
    resolved, _ = apply_research_answers(bundle, meeting(), {"K03:p1": {"action": "leave_unknown"}})
    assert [f.subject_ref for f in resolved.facts] == ["company"]
    assert not resolved.clarifications and any("übersprungen" in g for g in resolved.gaps)


async def test_assessment_no_tools_keeps_private_context_out_of_public_search():
    response = Response()
    response.output_parsed = AssessedInput(
        issues=[InputIssue(field="goal", question="Welches Ergebnis?", reason="Mehrdeutig")]
    )
    responses = Responses(response)
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    original = meeting()
    result = await provider.assess_input(original, Budget())
    assert result.evaluated and result.issues[0].question_id == "K04:goal"
    assert responses.calls[0]["tools"] == []
    assert responses.calls[0]["tool_choice"] == "none"
    assert original.goal == meeting().goal


async def test_assessment_failure_is_explicit_not_successful_empty_result():
    provider = Provider(settings(), client=SimpleNamespace())

    async def fail(*args, **kwargs):
        raise ProviderError("provider_schema_invalid")

    provider._request = fail
    assessed = await provider.assess_input(meeting(), Budget())
    assert not assessed.evaluated and assessed.limitation


async def test_supported_review_is_separate_tool_free_and_excludes_user_facts():
    response = Response()
    response.output_parsed = ReviewedEvidence(
        verdicts=[
            EvidenceVerdict(fact_id="candidate-1", verdict="supported", reason="Correct entity and amount")
        ]
    )
    responses = Responses(response)
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    bundle = bundle_fact()
    bundle.facts.append(
        Fact(fact_id="private-user-fact", field="goal", value="private-answer", provenance="user_confirmed")
    )
    result = await provider.review_evidence(ResearchQuery.from_input(meeting()), bundle, Budget())
    assert result.facts[0].status == "verified"
    call = responses.calls[0]
    assert call["tools"] == [] and call["tool_choice"] == "none"
    assert "private-answer" not in call["input"] and "TEST-INTERN-4711" not in call["input"]


@pytest.mark.parametrize("mode", ["missing", "duplicate", "invalid", "budget"])
async def test_review_fails_closed_per_candidate_preserving_evidence(mode):
    provider = Provider(settings(), client=SimpleNamespace())

    async def review(*args, **kwargs):
        if mode == "budget":
            raise BudgetExceeded("controlled")
        if mode == "invalid":
            return {"unexpected": True}
        verdict = EvidenceVerdict(fact_id="candidate-1", verdict="supported", reason="quote")
        return ReviewedEvidence(verdicts=[] if mode == "missing" else [verdict, verdict])

    provider._request = review
    original = bundle_fact()
    result = await provider.review_evidence(ResearchQuery.from_input(meeting()), original, Budget())
    assert result.facts[0].status == "unknown"
    assert result.sources[0].excerpt == original.sources[0].excerpt
    assert result.facts[0].evidence_ids == ["s1"] and result.evidence_review[0].verdict == "unclear"
    assert original.facts[0].status == "verified"


async def test_lexical_negation_overrides_erroneous_supported_semantic_verdict():
    bundle = bundle_fact()
    bundle.facts[0].field, bundle.facts[0].value = "current_role", "Geschäftsführerin"
    bundle.sources[0].excerpt = "Lea Sommer ist 2026 nicht Geschäftsführerin bei Musterwerk GmbH."
    response = Response()
    response.output_parsed = ReviewedEvidence(
        verdicts=[EvidenceVerdict(fact_id="candidate-1", verdict="supported", reason="Wrong reviewer")]
    )
    result = await Provider(
        settings(), client=SimpleNamespace(responses=Responses(response))
    ).review_evidence(ResearchQuery.from_input(meeting()), bundle, Budget())
    assert result.facts[0].status == "conflicting"
    assert result.evidence_review[0].verdict == "contradicted"


async def test_fixture_full_case_survives_separate_controlled_review():
    s = settings()
    s.fixture_mode = True
    provider, budget, query = FixtureProvider(s), Budget(), ResearchQuery.from_input(meeting())
    bundle = await provider.research(query, budget)
    reviewed = await provider.review_evidence(query, bundle, budget)
    assert reviewed.evidence_review and all(v.verdict == "supported" for v in reviewed.evidence_review)


async def test_material_gap_triggers_one_supplement_even_with_two_sources():
    events = []

    class Fetcher:
        async def fetch(self, url, budget):
            budget.reserve(evidence=1)
            return Source(source_id=url, url=url, title="public", excerpt="Public source text", verified=True)

    class Controlled(Provider):
        async def _request(self, budget, **kwargs):
            events.append(("search", kwargs["data"]))
            budget.reserve(model=1, searches=4)
            urls = ["https://press.example.org/a"] if len(events) == 1 else ["https://press.example.org/b"]
            return {
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"sources": [{"url": u} for u in urls]},
                    }
                ]
            }

        async def _extract_sources(self, query, sources, budget):
            events.append(("extract", [s.url for s in sources]))
            budget.reserve(model=1)
            return Extraction(facts=[], ambiguous_participant_ids=[], gaps=["Company facts missing"])

    provider = Controlled(settings(), client=SimpleNamespace(), fetcher=Fetcher())
    result = await provider.research(ResearchQuery.from_input(meeting()), Budget())
    assert [e[0] for e in events] == ["search", "extract", "search", "extract"]
    assert "employees_total" in events[2][1]["research_targets"]
    assert "TEST-INTERN-4711" not in json.dumps(events)
    assert result.supplemental_search_performed


async def test_supplement_does_not_spend_review_and_synthesis_model_slots():
    events = []

    class Controlled(Provider):
        async def _request(self, budget, **kwargs):
            events.append("search")
            budget.reserve(model=1, searches=4)
            return {"output": []}

    provider = Controlled(settings(), client=SimpleNamespace(), fetcher=SimpleNamespace())

    class Fetcher:
        async def fetch(self, url, budget):
            return source()

    async def extraction(*args):
        args[-1].reserve(model=1)
        return Extraction(facts=[], ambiguous_participant_ids=[], gaps=[])

    provider.fetcher, provider._extract_sources = Fetcher(), extraction
    budget = Budget()
    budget.usage.model_requests = 4
    result = await provider.research(ResearchQuery.from_input(meeting()), budget)
    assert events == ["search"] and not result.supplemental_search_performed
    assert budget.usage.model_requests == 6
