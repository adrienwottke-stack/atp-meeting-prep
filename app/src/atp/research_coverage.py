"""Content coverage from available evidence, without inventing research success."""

from .models import Fact, ResearchBundle, ResearchCoverage
from .rules import ICP_FIELDS, _facts_by_field, _leadership_from_components

DEVELOPMENT_FIELDS = {"business_development", "development", "ai_project", "digitalization_project"}
OWNERSHIP_FIELDS = {"family_ownership", "family_owned"}
LEADERSHIP_FIELDS = {"owner_led", "family_led", "owner_or_family_led"}
COMPANY_DESCRIPTION_FIELDS = {"business_model", "services", "industry", "markets", "customers"}
PERSON_FIELDS = {"current_role", "historical_role", "undated_role", "career", "professional_topic"}


def usable_facts(bundle: ResearchBundle, *, historical=False) -> list[Fact]:
    """Require actual source references or explicit user confirmation metadata."""
    sources = {s.source_id for s in bundle.sources if s.verified}
    statuses = {"verified", "historical"} if historical else {"verified"}
    return [
        f
        for f in bundle.facts
        if f.status in statuses
        and (
            (f.provenance == "web_verified" and bool(f.evidence_ids) and set(f.evidence_ids) <= sources)
            or (f.provenance == "user_confirmed" and f.confirmed_by and f.confirmed_at)
        )
    ]


def build_coverage(bundle: ResearchBundle) -> list[ResearchCoverage]:
    facts = usable_facts(bundle)
    company = {f.field for f in facts if f.subject_ref == "company"}
    leadership = _leadership_from_components(_facts_by_field(facts))
    if leadership is not None and leadership.status == "verified":
        company.add("owner_or_family_led")
    developments = [f for f in facts if f.subject_ref == "company" and f.field in DEVELOPMENT_FIELDS]
    people = {f.subject_ref for f in bundle.facts if f.subject_ref != "company"}
    people |= {q.participant_id for q in bundle.clarifications if q.participant_id}
    ready = {
        person
        for person in people
        if any(f.subject_ref == person and f.field == "current_role" for f in facts)
        and any(f.subject_ref == person and f.field in {"career", "professional_topic"} for f in facts)
        and not any(q.code == "K03" and q.participant_id == person for q in bundle.clarifications)
    }
    descriptions = company & COMPANY_DESCRIPTION_FIELDS
    icp = company & set(ICP_FIELDS)
    definitions = [
        (
            "company",
            "covered"
            if {"business_model", "services"} <= company
            else "partial"
            if descriptions
            else "missing",
            []
            if {"business_model", "services"} <= company
            else ["Geschäftsmodell und Leistungen sind noch nicht gemeinsam belastbar beschrieben."],
        ),
        (
            "developments",
            "covered"
            if any(f.information_as_of for f in developments)
            else "partial"
            if developments
            else "missing",
            []
            if any(f.information_as_of for f in developments)
            else ["Keine ausreichend belegte Entwicklung mit bestätigtem Zeitbezug vorhanden."],
        ),
        (
            "people",
            "covered"
            if people and ready == people
            else "partial"
            if any(f.subject_ref in people for f in facts)
            else "missing",
            []
            if people and ready == people
            else [
                "Aktuelle Rolle, beruflicher Hintergrund oder eindeutige Personenidentität sind noch offen."
            ],
        ),
        (
            "icp",
            "covered" if set(ICP_FIELDS) <= company else "partial" if icp else "missing",
            []
            if set(ICP_FIELDS) <= company
            else [
                "Offene Zielgruppenangaben: "
                + ", ".join(field for field in ICP_FIELDS if field not in company)
            ],
        ),
    ]
    result = []
    for area, status, reasons in definitions:
        if status != "covered":
            reasons += bundle.limits
        result.append(ResearchCoverage(area=area, status=status, reasons=list(dict.fromkeys(reasons))))
    return result
