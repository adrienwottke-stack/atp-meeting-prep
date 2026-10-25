"""Currency restrictions must survive real extraction/review entrypoints.

Controlled public-shaped evidence only; no network or model calls.
"""

import json
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from test_pdf import sample_result

import atp.providers as providers
from atp.models import (
    BriefingResult,
    ClarificationPlan,
    EvidenceVerdict,
    Fact,
    OverviewItem,
    ResearchBundle,
    ResearchQuery,
    Source,
)
from atp.providers import ExtractedFact, Extraction, Provider, ReviewedEvidence, validate_extraction
from atp.workflow import decorate_result


class EvaluatedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 15)


def query():
    return ResearchQuery(
        company_name="Schaeffler AG",
        domain="schaeffler.example",
        people=[{"participant_id": "schick", "name": "Andreas Schick"}],
    )


QUOTE = (
    "Schaeffler Group organizational and leadership structure since January 1, 2026 "
    "Andreas Schick Production, SCM & Purchasing"
)


def source():
    return Source(
        source_id="organization-chart",
        title="Controlled dated organization chart",
        url="https://schaeffler.example/organization.pdf",
        verified=True,
        excerpt=QUOTE,
    )


def test_extraction_keeps_quote_but_year_only_orgchart_role_is_not_current(monkeypatch):
    monkeypatch.setattr(providers, "date", EvaluatedDate)
    extraction = Extraction(
        facts=[
            ExtractedFact(
                subject_ref="schick",
                field="current_role",
                value_json=json.dumps("Production, SCM & Purchasing"),
                source_id="organization-chart",
                quote=QUOTE,
                information_as_of="2026",
                historical=False,
                identity_company="Schaeffler",
                identity_additional="Production, SCM & Purchasing",
            )
        ],
        ambiguous_participant_ids=[],
        gaps=[],
    )
    result = validate_extraction(extraction, [source()], query())
    assert len(result.facts) == 1
    role = result.facts[0]
    assert role.field == "undated_role"
    assert role.status == "verified" and role.provenance == "web_verified"
    assert role.information_as_of == "2026"
    assert role.supporting_quote == QUOTE
    assert result.sources[0].excerpt == QUOTE
    assert role.evidence_ids == ["organization-chart"]
    assert any("aktuelle berufliche Rolle" in gap for gap in result.gaps)


async def test_supported_model_review_cannot_promote_legacy_role_without_quote(monkeypatch):
    monkeypatch.setattr(providers, "date", EvaluatedDate)
    original = ResearchBundle(
        sources=[source()],
        facts=[
            Fact(
                fact_id="legacy-role",
                subject_ref="schick",
                field="current_role",
                value="Production, SCM & Purchasing",
                status="verified",
                provenance="web_verified",
                evidence_ids=["organization-chart"],
                information_as_of="2026-10-01",
                supporting_quote="",
            )
        ],
    )
    before = deepcopy(original.model_dump())
    calls = []
    provider = Provider(
        SimpleNamespace(openai_api_key="controlled-offline-only", model_extraction="controlled-model"),
        client=SimpleNamespace(),
        fetcher=SimpleNamespace(),
    )

    async def supported(*args, **kwargs):
        calls.append(kwargs)
        return ReviewedEvidence(
            verdicts=[
                EvidenceVerdict(
                    fact_id="legacy-role",
                    verdict="supported",
                    reason="Controlled overly permissive model verdict",
                )
            ]
        )

    monkeypatch.setattr(provider, "_request", supported)
    result = await provider.review_evidence(query(), original, object())
    assert len(calls) == 1
    assert calls[0]["data"]["evaluation_date"] == "2026-10-15"
    assert calls[0]["data"]["candidates"][0]["field"] == "undated_role"
    assert result.facts[0].field == "undated_role"
    assert result.facts[0].status == "verified"
    assert result.evidence_review[0].verdict == "supported"
    assert result.facts[0].supporting_quote == ""
    assert any("aktuelle berufliche Rolle" in gap for gap in result.gaps)
    assert original.model_dump() == before


@pytest.mark.parametrize(
    "verdict,expected_status", [("supported", "verified"), ("contradicted", "conflicting")]
)
async def test_recent_speaker_heading_reaches_semantic_review_as_current_but_remains_reviewable(
    monkeypatch, verdict, expected_status
):
    monkeypatch.setattr(providers, "date", EvaluatedDate)
    quote = (
        "08.09.2026 Dankesrede von Dr. Nicola Leibinger-Kammüller "
        "Vorsitzende des Vorstands TRUMPF SE + Co. KG"
    )
    original = ResearchBundle(
        sources=[
            Source(
                source_id="speech",
                title="Dated speech",
                url="https://trumpf.example/speech.pdf",
                verified=True,
                excerpt=quote,
            )
        ],
        facts=[
            Fact(
                fact_id="dated-role",
                subject_ref="nicola",
                field="current_role",
                value="Vorsitzende des Vorstands",
                status="verified",
                provenance="web_verified",
                evidence_ids=["speech"],
                information_as_of="08.09.2026",
                supporting_quote=quote,
            )
        ],
    )
    provider = Provider(
        SimpleNamespace(openai_api_key="controlled-offline-only"),
        client=SimpleNamespace(),
        fetcher=SimpleNamespace(),
    )
    calls = []

    async def review(*args, **kwargs):
        calls.append(kwargs)
        return ReviewedEvidence(
            verdicts=[
                EvidenceVerdict(
                    fact_id="dated-role", verdict=verdict, reason="Controlled independent evidence verdict"
                )
            ]
        )

    monkeypatch.setattr(provider, "_request", review)
    result = await provider.review_evidence(
        ResearchQuery(
            company_name="TRUMPF SE + Co. KG",
            domain="trumpf.example",
            people=[{"participant_id": "nicola", "name": "Nicola Leibinger-Kammüller"}],
        ),
        original,
        object(),
    )
    assert calls[0]["data"]["candidates"][0]["field"] == "current_role"
    assert calls[0]["data"]["candidates"][0]["information_as_of"] == "08.09.2026"
    assert "180 Tagen" in calls[0]["instructions"]
    assert "exakten heutigen Prüftag" in calls[0]["instructions"]
    assert "undated_role" in calls[0]["instructions"]
    assert result.facts[0].field == "current_role"
    assert result.facts[0].status == expected_status


def test_final_overview_cannot_turn_undated_role_into_model_authored_current_claim():
    result = sample_result()
    result.facts.append(
        Fact(
            fact_id="undated-person-role",
            subject_ref="p1",
            field="undated_role",
            value="CEO",
            status="verified",
            provenance="web_verified",
            evidence_ids=["s1"],
            information_as_of="2026",
        )
    )
    unsafe_text = "Lea Sommer ist aktuell CEO des Unternehmens."
    result.overview = [
        OverviewItem(
            category="people",
            text=unsafe_text,
            kind="fact",
            fact_ids=["undated-person-role"],
            evidence_ids=["s1"],
        )
    ]
    # References and fact status are structurally valid, so the final deterministic
    # assembly must protect role currency rather than rely on schema validation.
    result = BriefingResult.model_validate(result.model_dump(mode="json"))
    final = decorate_result(
        result,
        result.input,
        ResearchBundle(sources=result.sources, facts=result.facts),
        result.icp,
        result.recommendation,
        ClarificationPlan(),
        {},
    )
    people = next(item for item in final.overview if item.category == "people")
    assert people.kind == "unknown"
    assert unsafe_text not in people.text
    assert "nicht belastbar bestätigt" in people.text
    assert all(profile.status == "unresolved" for profile in final.people)
    assert not any(unsafe_text in text for text in final.summary)
