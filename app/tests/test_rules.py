import json
from pathlib import Path

import pytest

from atp.models import Fact, MeetingInput, RecommendationValue, ResearchBundle, utcnow
from atp.profiles import load_profile, profile_hash
from atp.rules import clarification_for_crm, clarify_input, evaluate_icp, recommend, research_clarifications


def meeting(**changes):
    value = {
        "company": {"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
        "people": [{"name": "Lea Sommer"}, {"name": "Tobias Kern"}],
        "meeting_type": "setup",
        "relationship": "new_contact",
        "goal": "Nächsten Schritt klären",
        "confirmed": True,
        "public_identifiers_confirmed": True,
    }
    value.update(changes)
    return MeetingInput.model_validate(value)


def facts(**changes):
    values = {
        "employees_total": 120,
        "knowledge_workers": 40,
        "owner_or_family_led": True,
        "segment": "Mittelstand",
        "is_corporation": False,
        "is_public_authority": False,
    }
    values.update(changes)
    return [
        Fact(
            field=field,
            value=value,
            status="verified",
            provenance="web_verified",
            evidence_ids=["s1"],
            information_as_of="2026",
        )
        for field, value in values.items()
        if value is not None
    ]


@pytest.mark.parametrize(
    "size,status",
    [
        (19, "deviating"),
        (20, "matching"),
        (3000, "matching"),
        (3001, "deviating"),
        ({"min": 10, "max": 50}, "unclear"),
    ],
)
def test_t06_employee_boundaries(size, status):
    data = meeting()
    icp = evaluate_icp(facts(employees_total=size), data, load_profile())
    assert icp.status == status
    assert recommend(data, icp).value != RecommendationValue.DECLINE
    if status == "deviating":
        assert icp.deviation_kinds == ["corridor"]
        assert recommend(data, icp).value == RecommendationValue.EXCEPTION


@pytest.mark.parametrize(
    "span,status",
    [
        ({"min": 20, "max": 3000}, "matching"),
        ({"min": 3000, "max": 3001}, "unclear"),
        ({"min": 1, "max": 19}, "deviating"),
    ],
)
def test_spans(span, status):
    assert evaluate_icp(facts(employees_total=span), meeting(), load_profile()).status == status


def test_t05_unknown_leadership_is_not_negative():
    data = meeting()
    icp = evaluate_icp(facts(owner_or_family_led=None), data, load_profile())
    assert icp.status == "unclear"
    assert next(c for c in icp.criteria if c.field == "owner_or_family_led").status == "unclear"
    assert recommend(data, icp).value == RecommendationValue.CLARIFY


def test_managing_director_or_same_name_does_not_prove_ownership():
    evidence = facts(owner_or_family_led="Geschäftsführer: Herr Muster")
    assert evaluate_icp(evidence, meeting(), load_profile()).status == "unclear"


def test_unbacked_web_facts_and_unconfirmed_user_fields_ignored():
    evidence = facts()
    evidence[0].evidence_ids = []
    assert evaluate_icp(evidence, meeting(), load_profile()).status == "unclear"
    evidence[0].provenance = "user_confirmed"
    assert evaluate_icp(evidence, meeting(), load_profile()).status == "unclear"
    evidence[0].confirmed_by, evidence[0].confirmed_at = "actor", utcnow()
    assert evaluate_icp(evidence, meeting(), load_profile()).status == "matching"


def test_subsidiary_does_not_inherit_group_headcount():
    evidence = facts(employees_total=None)
    evidence.append(
        Fact(
            subject_ref="parent_group",
            field="employees_total",
            value=120,
            status="verified",
            provenance="web_verified",
            evidence_ids=["s1"],
        )
    )
    assert evaluate_icp(evidence, meeting(), load_profile()).status == "unclear"


def test_conflict_not_resolved_by_source_order():
    evidence = facts()
    evidence.append(
        Fact(
            field="employees_total",
            value=5000,
            status="verified",
            provenance="web_verified",
            evidence_ids=["s2"],
        )
    )
    icp = evaluate_icp(evidence, meeting(), load_profile())
    assert next(c for c in icp.criteria if c.field == "employees_total").status == "unclear"


def test_t07_corporation_new_contact_and_exception():
    data = meeting()
    icp = evaluate_icp(facts(is_corporation=True), data, load_profile())
    assert icp.status == "deviating"
    assert "exclusion" in icp.deviation_kinds
    assert recommend(data, icp).value == RecommendationValue.DECLINE
    data.exception_reason = "ATP-Fachbereich hat konkreten Gesprächsnutzen angefragt."
    assert recommend(data, icp).value == RecommendationValue.EXCEPTION


def test_t08_ongoing_project_despite_corporation():
    data = meeting(
        relationship="ongoing_project", meeting_type="project_review", goal="Umsetzungshindernisse klären"
    )
    icp = evaluate_icp(facts(is_corporation=True), data, load_profile())
    assert recommend(data, icp).value == RecommendationValue.ATTEND
    assert icp.entry_service_ids == ["enablement"]
    data.goal = ""
    assert recommend(data, icp).value == RecommendationValue.CLARIFY


def test_existing_obligation_vs_new_sales_vs_unspecified():
    data = meeting(relationship="existing_customer")
    icp = evaluate_icp(facts(is_corporation=True), data, load_profile())
    assert recommend(data, icp).value == RecommendationValue.CLARIFY
    assert any(
        q.field == "relationship_context" for q in research_clarifications(data, ResearchBundle(), icp)
    )
    data.relationship_context = "existing_obligation"
    assert recommend(data, icp).value == RecommendationValue.ATTEND
    data.relationship_context = "new_sales_inquiry"
    assert recommend(data, icp).value == RecommendationValue.DECLINE


def test_unknown_relationship_only_when_it_changes_recommendation():
    data = meeting(relationship="unknown")
    matching = evaluate_icp(facts(), data, load_profile())
    assert recommend(data, matching).value == RecommendationValue.ATTEND
    deviating = evaluate_icp(facts(is_corporation=True), data, load_profile())
    assert recommend(data, deviating).value == RecommendationValue.CLARIFY
    assert any(q.code == "K04" for q in research_clarifications(data, ResearchBundle(), deviating))


def test_t09_career_gap_does_not_block_meeting():
    data = meeting()
    icp = evaluate_icp(facts(), data, load_profile())
    assert recommend(data, icp).value == RecommendationValue.ATTEND
    assert not research_clarifications(data, ResearchBundle(gaps=["Karriereschritt fehlt"]), icp)


def test_t10_workforce_not_knowledge_workers():
    data = meeting()
    icp = evaluate_icp(facts(employees_total=25, knowledge_workers=None), data, load_profile())
    assert icp.status == "matching"
    assert icp.entry_service_ids == ["entry"]
    assert "Wissensarbeiter" in icp.entry_reason
    assert recommend(data, icp).value == RecommendationValue.ATTEND
    assert evaluate_icp(facts(knowledge_workers=29), data, load_profile()).entry_service_ids == [
        "training",
        "workshop",
    ]
    assert evaluate_icp(facts(knowledge_workers=30), data, load_profile()).entry_service_ids == ["audit"]


def test_t02_input_core_and_invalid_urls():
    for company in (
        {"name": "Musterwerk GmbH", "website": ""},
        {"name": "", "website": "https://musterwerk.example"},
        {"name": "Musterwerk", "website": "javascript:alert(1)"},
    ):
        assert all(q.code == "K01" for q in clarify_input(meeting(company=company)))
    assert any(q.code == "K01" for q in clarify_input(meeting(people=[])))
    assert any(q.code == "K01" for q in clarify_input(meeting(meeting_type=None)))


def test_t03_company_mismatch_confirmation_and_k03_independence():
    data = meeting(company={"name": "Firma Alpha GmbH", "website": "https://beta.example"})
    assert any(q.code == "K02" and q.handling == "blocking_company" for q in clarify_input(data))
    data.company.entity_hint = "Firma und Website vom Nutzer bestätigt"
    assert not any(q.code == "K02" for q in clarify_input(data))
    crm = clarification_for_crm(["Firma A", "Firma B"])
    assert len(crm) == 1 and crm[0].code == "K05" and crm[0].handling == "blocking_transfer"


def test_profile_is_exact_catalog_and_independent_snapshot():
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads(
        (
            root
            / "docs/archive/initial-package/docs/ATP-Anforderungskatalog-Meeting-Vorbereitungs-Agent-v0.3.json"
        ).read_text(encoding="utf-8")
    )
    profile = load_profile()
    assert profile == catalog["atp_reference_profile"]
    before = profile_hash(profile)
    profile["services"][0]["name"] = "Changed"
    assert profile_hash(load_profile()) == before
    assert load_profile()["status"].startswith("Entwurf")
    with pytest.raises(ValueError):
        load_profile("../secret")
