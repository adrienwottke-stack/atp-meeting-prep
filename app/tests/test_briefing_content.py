from copy import deepcopy

from test_pdf import sample_result

from atp.briefing_content import build_overview
from atp.models import Fact, ResearchBundle, utcnow
from atp.research_coverage import build_coverage


def add_fact(result, field, value, *, subject="company", **kwargs):
    fact = Fact(
        subject_ref=subject,
        field=field,
        value=value,
        provenance="web_verified",
        evidence_ids=["s1"],
        status="verified",
        **kwargs,
    )
    result.facts.append(fact)
    return fact


def test_overview_distinct_five_priorities_valid_references_and_immutable_legacy():
    result = sample_result()
    before = deepcopy(result.model_dump())
    overview = build_overview(result)
    assert [i.category for i in overview] == ["company", "development", "people", "opener", "next_step"]
    assert all(i.text.strip() for i in overview)
    assert result.model_dump() == before
    assert sum(result.recommendation.value in i.text for i in overview) == 1
    assert all(set(i.fact_ids) <= {f.fact_id for f in result.facts} for i in overview)
    assert all(set(i.evidence_ids) <= {s.source_id for s in result.sources} for i in overview)
    assert overview[0].kind == "fact"
    assert overview[2].kind == "unknown"
    assert overview[3].kind == "hypothesis"


def test_family_ownership_does_not_become_operational_leadership():
    result = sample_result()
    add_fact(result, "family_ownership", True)
    text = build_overview(result)[0].text
    assert "Familienbesitz: ausdrücklich bestätigt" in text
    assert "Operative" not in text
    add_fact(result, "family_led", False)
    text = build_overview(result)[0].text
    assert "Operative Familienführung: ausdrücklich verneint" in text


def test_historical_development_never_establishes_current_need():
    result = sample_result()
    fact = add_fact(
        result, "ai_project", "Zusammenarbeit mit Forschungspartner", information_as_of="2023-05-01"
    )
    development = build_overview(result)[1]
    assert "Historischer Kontext (2023-05-01)" in development.text
    assert "heutiger Bedarf ist damit nicht bestätigt" in development.text
    assert development.fact_ids == [fact.fact_id]


def test_reference_date_older_than_twentyfour_months_is_historical():
    result = sample_result()
    add_fact(result, "business_development", "Neue Fertigung", information_as_of="2024-01-01")
    assert "Historischer Kontext" in build_overview(result)[1].text


def test_unknown_or_unbacked_source_never_promoted_in_overview_or_coverage():
    result = sample_result()
    result.sources[0].verified = False
    overview = build_overview(result)
    assert overview[0].kind == "unknown"
    assert overview[0].fact_ids == []
    coverage = build_coverage(ResearchBundle(sources=result.sources, facts=result.facts))
    assert coverage[0].status == "missing"


def test_confirmed_role_is_attached_to_correct_person():
    result = sample_result()
    role = add_fact(result, "current_role", "Leiterin Fertigung", subject="p1")
    result.people[0].status = "confirmed"
    item = build_overview(result)[2]
    assert "Lea Sommer: Leiterin Fertigung" in item.text
    assert "Tobias Groß: aktuelle Rolle" in item.text
    assert item.fact_ids == [role.fact_id]
    assert item.kind == "unknown"  # One participant still has unresolved identity.


def test_user_confirmed_company_info_retains_provenance():
    result = sample_result()
    fact = result.facts[0]
    fact.provenance, fact.evidence_ids = "user_confirmed", []
    fact.confirmed_by, fact.confirmed_at = "test-user", utcnow()
    item = build_overview(result)[0]
    assert item.kind == "user_confirmed"
    assert item.evidence_ids == []


def test_empty_bundle_has_four_explicit_missing_areas():
    coverage = build_coverage(ResearchBundle(limits=["Quelle technisch nicht abrufbar"]))
    assert [i.area for i in coverage] == ["company", "developments", "people", "icp"]
    assert all(i.status == "missing" and i.reasons for i in coverage)
    assert all("Quelle technisch nicht abrufbar" in i.reasons for i in coverage)


def test_coverage_requires_distinct_information_areas():
    result = sample_result()
    add_fact(result, "services", ["Baugruppen", "Werkzeugbau"])
    add_fact(result, "business_development", "Neue Halle", information_as_of="2026")
    add_fact(result, "current_role", "Fertigungsleiterin", subject="p1")
    add_fact(result, "career", "Seit 2025 im Unternehmen", subject="p1")
    coverage = {c.area: c for c in build_coverage(ResearchBundle(sources=result.sources, facts=result.facts))}
    assert coverage["company"].status == "covered"
    assert coverage["developments"].status == "covered"
    assert coverage["people"].status == "covered"
    assert coverage["icp"].status == "missing"


def test_undated_development_is_partial_and_described_as_undated():
    result = sample_result()
    add_fact(result, "ai_project", "Automatisierungsprojekt")
    assert "Zeitbezug offen" in build_overview(result)[1].text
    coverage = {c.area: c for c in build_coverage(ResearchBundle(sources=result.sources, facts=result.facts))}
    assert coverage["developments"].status == "partial"
