from copy import deepcopy

from atp.gap_reconciliation import reconcile_gaps
from atp.models import Clarification, EvidenceVerdict, Fact, MeetingInput, ResearchBundle, Source


def case():
    data = MeetingInput(
        company={"name": "Controlled AG", "website": "https://controlled.example"},
        people=[
            {"participant_id": "p1", "name": "Klaus Rosenfeld"},
            {"participant_id": "p2", "name": "Andreas Schick"},
        ],
        meeting_type="setup",
    )
    bundle = ResearchBundle(
        sources=[
            Source(
                source_id="s1",
                title="Controlled final evidence",
                url="https://controlled.example",
                verified=True,
            )
        ]
    )
    return data, bundle


def fact(bundle, field, value="Controlled fact", *, subject="company", **changes):
    item = Fact(
        field=field,
        value=value,
        subject_ref=subject,
        status="verified",
        provenance="web_verified",
        evidence_ids=["s1"],
        **changes,
    )
    bundle.facts.append(item)
    return item


def test_later_dated_role_clears_only_its_own_stale_gap_not_another_person_or_background():
    data, bundle = case()
    fact(bundle, "current_role", "Chief Executive Officer", subject="p1", information_as_of="2026")
    stale = "Für die aktuelle Rolle von Klaus Rosenfeld liegt kein sicher datierter aktueller Beleg vor."
    schick = "Für Andreas Schick liegt keine konkrete Rolle oder ein weiteres berufliches Merkmal im bereitgestellten Text vor."
    career = "Klaus Rosenfeld: beruflicher Werdegang nicht ausreichend belegt."
    bundle.gaps = [stale, schick, career]
    before = deepcopy(bundle.model_dump())
    result = reconcile_gaps(bundle, data)
    assert stale not in result.gaps
    assert schick in result.gaps and career in result.gaps
    assert any("Klaus Rosenfeld" in gap and "Themen" in gap for gap in result.gaps)
    assert not any(f.subject_ref == "p2" and f.field == "current_role" for f in result.facts)
    assert bundle.model_dump() == before


def test_field_prefixed_rejection_is_superseded_but_technical_limits_and_conflicts_stay():
    data, bundle = case()
    fact(bundle, "industry")
    rejected = "industry: kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen."
    conflict = "industry: widersprüchliche Quellen."
    technical = "industry: HTTP 403 beim Abruf einer zusätzlichen Quelle."
    bundle.gaps = [
        rejected,
        conflict,
        technical,
        "locations: kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen.",
    ]
    bundle.limits = ["Beleg nicht nutzbar (evidence_http_403): https://controlled.example/blocked"]
    result = reconcile_gaps(bundle, data)
    assert rejected not in result.gaps
    assert conflict in result.gaps and technical in result.gaps
    assert any(gap.startswith("locations:") for gap in result.gaps)
    assert result.limits == bundle.limits


def test_conflicting_or_different_verified_values_do_not_clear_missing_gap():
    data, bundle = case()
    fact(bundle, "employees_total", 100)
    fact(bundle, "employees_total", 5000)
    gap = "employees_total: kein eindeutiger belegter Nachweis."
    bundle.gaps = [gap]
    assert gap in reconcile_gaps(bundle, data).gaps
    bundle.facts[-1].status = "conflicting"
    assert gap in reconcile_gaps(bundle, data).gaps


def test_unscoped_person_gap_requires_all_requested_people():
    data, bundle = case()
    fact(bundle, "current_role", subject="p1", information_as_of="2026")
    gap = "current_role: kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen."
    bundle.gaps = [gap]
    assert gap in reconcile_gaps(bundle, data).gaps
    fact(bundle, "current_role", subject="p2", information_as_of="2026")
    assert gap not in reconcile_gaps(bundle, data).gaps


def test_undated_or_historical_role_never_clears_current_role_gap():
    data, bundle = case()
    item = fact(bundle, "current_role", subject="p1")
    gap = "Klaus Rosenfeld: aktuelle berufliche Rolle nicht eindeutig mit Zeitbezug bestätigt."
    bundle.gaps = [gap]
    assert gap in reconcile_gaps(bundle, data).gaps
    item.status, item.information_as_of = "historical", "2018"
    assert gap in reconcile_gaps(bundle, data).gaps


def test_ambiguous_identity_and_rejected_review_never_clear_person_gap():
    data, bundle = case()
    item = fact(bundle, "current_role", subject="p1", information_as_of="2026")
    gap = "Klaus Rosenfeld: aktuelle Rolle nicht belegt."
    bundle.gaps = [gap]
    bundle.clarifications = [
        Clarification(
            code="K03",
            field="people",
            participant_id="p1",
            reason="Namensvetter",
            options=[],
            handling="skippable_profile",
        )
    ]
    assert gap in reconcile_gaps(bundle, data).gaps
    bundle.clarifications = []
    bundle.evidence_review = [
        EvidenceVerdict(fact_id=item.fact_id, verdict="contradicted", reason="Controlled contradiction")
    ]
    assert gap in reconcile_gaps(bundle, data).gaps


def test_markets_fact_does_not_invent_customers_and_partial_gap_is_rewritten():
    data, bundle = case()
    bundle.gaps = ["Keine hinreichend konkreten Belege zu Kunden oder Märkten."]
    fact(bundle, "markets", "Industrial markets")
    result = reconcile_gaps(bundle, data)
    assert "Keine hinreichend konkreten Belege zu Kunden oder Märkten." not in result.gaps
    assert "Kunden: keine ausreichend belegten Angaben." in result.gaps
    fact(bundle, "customers", "Business customers")
    assert "Kunden: keine ausreichend belegten Angaben." not in reconcile_gaps(bundle, data).gaps


def test_historical_development_and_parent_entity_do_not_clear_current_company_rejection():
    data, bundle = case()
    item = fact(bundle, "business_development", information_as_of="2020")
    item.status = "historical"
    fact(bundle, "employees_total", 100000, subject="parent_group")
    bundle.gaps = [
        "business_development: kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen.",
        "employees_total: kein eindeutiger belegter Nachweis.",
    ]
    result = reconcile_gaps(bundle, data)
    assert all(gap in result.gaps for gap in bundle.gaps)


def test_missing_people_are_added_explicitly_and_reconciliation_is_idempotent():
    data, bundle = case()
    result = reconcile_gaps(bundle, data)
    assert len(result.gaps) == 6
    assert all(any(person.name in gap for gap in result.gaps) for person in data.people)
    assert reconcile_gaps(result, data).model_dump() == result.model_dump()
