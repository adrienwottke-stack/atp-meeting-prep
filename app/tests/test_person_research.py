"""Regression cases for reachable posts and complete professional profiles."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError
from test_providers import Responses, extraction_fact, meeting, settings

from atp.budget import Budget
from atp.config import Settings
from atp.evidence import EvidenceError
from atp.models import EvidenceVerdict, ResearchQuery, Source
from atp.person_evidence import extraction_excerpts, extraction_sources, profile_bindings
from atp.providers import Extraction, Provider, ProviderError, ReviewedEvidence, validate_extraction
from atp.research_pipeline import evidence_queue, research_needs


def query(**kwargs):
    return ResearchQuery.from_input(
        meeting(people=[{"participant_id": "p1", "name": "Lea Sommer", **kwargs}])
    )


def profile(**kwargs):
    return Source(
        **{
            "source_id": "profile",
            "title": "Lea Sommer, Engineering und Automation",
            "url": "https://www.freelancermap.de/profil/lea-sommer",
            "verified": True,
            "excerpt": "Lea Sommer arbeitet im Kontext von Musterwerk GmbH. "
            "Sales Development Representative bei Anderswerk GmbH. "
            "Projektleitung des elektrischen Antriebsstrangs. "
            "Maschinenbaustudium an der Technischen Universität. "
            "Entwicklung von Workflows mit n8n und APIs.",
            **kwargs,
        }
    )


def career(source_id="profile", **kwargs):
    return extraction_fact(
        subject_ref="p1",
        field="career",
        source_id=source_id,
        quote="Projektleitung des elektrischen Antriebsstrangs.",
        value_json='"Projektleitung des elektrischen Antriebsstrangs"',
        identity_company=None,
        identity_additional=None,
        information_as_of=None,
        **kwargs,
    )


@pytest.mark.parametrize(
    "invalid", [None, "unverified", "other_person", "other_company", "feed", "duplicate"]
)
def test_profile_binds_sections_without_repeating_name_and_company_in_every_quote(invalid):
    s, q = profile(), query()
    if invalid == "unverified":
        s.verified = False
    if invalid == "other_person":
        s.title = "Lea Winter, Engineering"
    if invalid == "other_company":
        s.excerpt = s.excerpt.replace("Musterwerk GmbH", "Fremdwerk GmbH")
    if invalid == "feed":
        s.url = "https://www.linkedin.com/company/musterwerk"
    if invalid == "duplicate":
        q.people.append({"participant_id": "p2", "name": "Lea Sommer"})
    bundle = validate_extraction(Extraction(facts=[career()], ambiguous_participant_ids=[], gaps=[]), [s], q)
    assert bool(bundle.facts) == (invalid is None)
    if bundle.facts:
        assert bundle.facts[0].supporting_quote == career().quote
        assert bundle.facts[0].information_as_of is None
        assert bundle.facts[0].field == "career"


def test_explicit_profile_url_can_bind_a_profile_at_another_company():
    s = profile(excerpt=profile().excerpt.replace("Musterwerk GmbH", "Fremdwerk GmbH"))
    assert not profile_bindings(query(), [s])
    assert profile_bindings(query(profile_url=s.url + "?tracking=1", public_identifiers_confirmed=True), [s])


def test_professional_post_can_use_employer_from_a_bound_profile_without_target_company():
    s = profile()
    quote = "Lea Sommer beschreibt bei Anderswerk GmbH die Entwicklung einer Validation Suite."
    post = Source(
        source_id="post",
        url="https://de.linkedin.com/posts/lea-sommer-123_validation",
        title="Lea Sommer",
        excerpt=quote,
        verified=True,
    )
    candidate = extraction_fact(
        subject_ref="p1",
        field="professional_topic",
        value_json='"Validation Suite"',
        source_id="post",
        quote=quote,
        identity_company="Anderswerk GmbH",
        identity_additional="Validation Suite",
        information_as_of=None,
    )
    bundle = validate_extraction(
        Extraction(facts=[candidate], ambiguous_participant_ids=[], gaps=[]), [s, post], query()
    )
    assert len(bundle.facts) == 1
    assert set(bundle.facts[0].evidence_ids) == {"profile", "post"}
    candidate.identity_company = "Unrelated GmbH"
    assert not validate_extraction(
        Extraction(facts=[candidate], ambiguous_participant_ids=[], gaps=[]), [s, post], query()
    ).facts


async def test_profile_keeps_entire_career_with_many_company_sources_in_extraction_request():
    s = profile(
        excerpt="Lea Sommer bei Musterwerk GmbH. "
        + "Beruflicher Kontext. " * 450
        + "Projektleitung des elektrischen Antriebsstrangs und Formula Student."
    )
    others = [
        Source(
            source_id=f"c{i}",
            url=f"https://musterwerk.example/page-{i}",
            title="Musterwerk GmbH",
            excerpt="Musterwerk GmbH entwickelt Anlagen. " * 300,
            verified=True,
        )
        for i in range(24)
    ]
    responses = Responses()
    responses.response.output_parsed = Extraction(facts=[], ambiguous_participant_ids=[], gaps=[])
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    await provider._extract_sources(query(), [*others, s], Budget(Settings(_env_file=None)))
    data = json.loads(responses.calls[0]["input"])
    selected = {s["source_id"]: s["excerpt"] for s in data["untrusted_sources"]}
    assert selected["profile"] == s.excerpt
    assert "Formula Student" in selected["profile"]
    assert data["person_profile_bindings"] == {"p1": ["profile"]}
    assert len(selected) <= 7 and sum(map(len, selected.values())) <= 64000


def test_irrelevant_titled_sources_do_not_enter_the_person_fetch_window():
    urls = [
        "https://foreign.example/nexara",
        profile().url,
        "https://directory.example/freelancers",
        "https://musterwerk.example/",
    ]
    titles = {
        urls[0]: "Unrelated Holding",
        urls[1]: profile().title,
        urls[2]: "Find 100,000 freelancers",
        urls[3]: "Musterwerk GmbH",
    }
    queue = evidence_queue(urls, query(), people_focus=True, titles=titles)
    assert set(queue) == {profile().url, "https://musterwerk.example/"}


def test_invalid_citation_metadata_is_skipped_without_aborting_relevant_person_sources():
    urls = ["javascript:alert(1)", "https://user:password@example.com/profile", profile().url]
    queue = evidence_queue(urls, query(), titles={url: "Lea Sommer" for url in urls})
    assert queue == [profile().url]


@pytest.mark.parametrize(
    "code,post_expected",
    [("evidence_http_999", True), ("evidence_http_403", True), ("evidence_http_429", False)],
)
async def test_profile_access_failure_preserves_posts_but_rate_limit_blocks_whole_host(code, post_expected):
    calls, searches = [], []
    person_profile = "https://de.linkedin.com/in/lea-sommer-123"
    post_url = "https://www.linkedin.com/posts/lea-sommer-123_validation"

    class Fetcher:
        async def fetch(self, url, budget):
            budget.reserve(evidence=1)
            calls.append(url)
            if "/in/" in url:
                raise EvidenceError(code)
            if "/posts/" in url:
                return profile(source_id="post", url=url)
            return profile(source_id="company", url=url, title="Musterwerk GmbH")

    class SearchProvider(Provider):
        async def _request(self, budget, **kwargs):
            budget.reserve(model=1, searches=4)
            searches.append(kwargs["data"])
            return {
                "output": [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {
                            "sources": [
                                {"url": person_profile, "title": "Lea Sommer"},
                                {"url": post_url, "title": "Lea Sommer"},
                            ]
                        },
                    }
                ]
            }

        async def _extract_sources(self, query, sources, budget):
            return Extraction(facts=[], ambiguous_participant_ids=[], gaps=[])

    settings = Settings(_env_file=None)
    provider = SearchProvider(settings, client=SimpleNamespace(), fetcher=Fetcher())
    await provider.research(query(), Budget(settings))
    assert calls.count(person_profile) == 1
    assert (post_url in calls) == post_expected
    if post_expected:
        assert "linkedin.com" not in searches[1]["unavailable_hosts"]
        assert any("site:linkedin.com/posts/" in q for q in searches[1]["search_queries"])
    else:
        assert "linkedin.com" in searches[1]["unavailable_hosts"]


def test_role_repetition_is_not_a_career_and_unknown_currency_does_not_trigger_endless_person_research():
    s = profile()
    role = extraction_fact(
        subject_ref="p1",
        field="undated_role",
        source_id="profile",
        value_json='"Sales Development Representative"',
        quote="Sales Development Representative bei Anderswerk GmbH.",
        identity_company=None,
        identity_additional=None,
        information_as_of=None,
    )
    repeated = role.model_copy(update={"field": "career"})
    topic = role.model_copy(
        update={
            "field": "professional_topic",
            "value_json": '"Workflows mit n8n und APIs"',
            "quote": "Entwicklung von Workflows mit n8n und APIs.",
        }
    )
    bundle = validate_extraction(
        Extraction(facts=[role, repeated, career(), topic], ambiguous_participant_ids=[], gaps=[]),
        [s],
        query(),
    )
    assert len([f for f in bundle.facts if f.field == "career"]) == 1
    assert next(c for c in research_needs(query(), bundle) if c.area == "people").status == "covered"
    assert all(f.field != "current_role" for f in bundle.facts)


async def test_independent_review_receives_shared_profile_once_and_retains_each_exact_quote():
    s = profile()
    study = career().model_copy(
        update={
            "quote": "Maschinenbaustudium an der Technischen Universität.",
            "value_json": '"Maschinenbaustudium an der Technischen Universität"',
        }
    )
    bundle = validate_extraction(
        Extraction(facts=[career(), study], ambiguous_participant_ids=[], gaps=[]), [s], query()
    )
    responses = Responses()
    responses.response.output_parsed = ReviewedEvidence(
        verdicts=[
            EvidenceVerdict(fact_id=f.fact_id, verdict="supported", reason="Literal profile passage")
            for f in bundle.facts
        ]
    )
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    result = await provider.review_evidence(query(), bundle, Budget(Settings(_env_file=None)))
    data = json.loads(responses.calls[0]["input"])
    assert len(data["sources"]) == 1 and len(data["candidates"]) == 2
    assert all(
        candidate["supporting_quote"] in data["sources"][0]["excerpt"] for candidate in data["candidates"]
    )
    assert all(candidate["evidence"] == [{"source_id": "profile"}] for candidate in data["candidates"])
    assert len(result.facts) == 2 and all(f.status == "verified" for f in result.facts)


async def test_quota_exhaustion_is_not_retried_as_a_transient_rate_limit():
    calls = []

    class EmptyQuota:
        async def create(self, **kwargs):
            calls.append(kwargs)
            raise APIStatusError(
                "quota",
                response=httpx.Response(429, request=httpx.Request("POST", "https://api.example.test")),
                body={"error": {"code": "insufficient_quota"}},
            )

    budget = Budget(Settings(_env_file=None))
    provider = Provider(settings(), client=SimpleNamespace(responses=EmptyQuota()))
    with pytest.raises(ProviderError, match="provider_insufficient_quota"):
        await provider._request(budget, model="test", instructions="test", data={})
    assert len(calls) == 1 and budget.usage.errors == ["provider_insufficient_quota"]
    with pytest.raises(ProviderError, match="provider_insufficient_quota"):
        await provider._request(budget, model="test", instructions="test", data={})
    assert len(calls) == 1 and budget.usage.model_requests == 1


def test_extraction_window_is_bounded_and_unverified_search_titles_cannot_bind_identity():
    s = profile(verified=False, retrieval_kind="search_index")
    assert not profile_bindings(query(), [s])
    selected = extraction_sources(query(), [profile()])
    assert extraction_excerpts(query(), selected)["profile"] == profile().excerpt


def test_two_same_name_profile_identities_are_not_silently_merged():
    a = profile(url="https://de.linkedin.com/in/lea-sommer-123")
    b = profile(source_id="other", url="https://www.linkedin.com/in/lea-sommer-456")
    assert not profile_bindings(query(), [a, b])
    candidate = career()
    candidate.quote = "Lea Sommer arbeitet im Kontext von Musterwerk GmbH."
    candidate.value_json = '"Musterwerk GmbH"'
    candidate.identity_company = "Musterwerk GmbH"
    candidate.identity_additional = "Kontext"
    bundle = validate_extraction(
        Extraction(facts=[candidate], ambiguous_participant_ids=[], gaps=[]), [a, b], query()
    )
    assert bundle.clarifications and all(f.status == "conflicting" for f in bundle.facts)
