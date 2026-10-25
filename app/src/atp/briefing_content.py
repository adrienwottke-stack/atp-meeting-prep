"""Readable fallback overview using only existing, attributable facts."""

import json
import re
from datetime import date
from hashlib import sha256

from .models import BriefingResult, OverviewItem, ResearchBundle
from .research_coverage import DEVELOPMENT_FIELDS, usable_facts

PRESENTATION_VERSION = "atp-presentation-4"
_LABELS = {
    "business_model": "Geschäft",
    "services": "Leistungen",
    "industry": "Branche",
    "markets": "Märkte",
    "customers": "Kunden",
    "family_ownership": "Familienbesitz",
    "family_owned": "Familienbesitz",
    "owner_led": "Operative Eigentümerführung",
    "family_led": "Operative Familienführung",
    "owner_or_family_led": "Eigentümer- oder Familienführung",
}


def _text(value) -> str:
    if isinstance(value, bool):
        return "ausdrücklich bestätigt" if value else "ausdrücklich verneint"
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _item(category, text, facts=(), *, kind=None):
    facts = list(facts)
    if kind is None:
        kind = (
            "user_confirmed"
            if any(f.provenance == "user_confirmed" for f in facts)
            else "fact"
            if facts
            else "unknown"
        )
    return OverviewItem(
        category=category,
        text=text,
        kind=kind,
        fact_ids=list(dict.fromkeys(f.fact_id for f in facts)),
        evidence_ids=list(dict.fromkeys(s for f in facts for s in f.evidence_ids)),
    )


def _year(value):
    found = re.search(r"\b(19\d{2}|20\d{2})\b", value or "")
    return int(found.group(0)) if found else 0


def _historical(fact, generated_at):
    if fact.status == "historical":
        return True
    try:
        published = date.fromisoformat(fact.information_as_of or "")
    except ValueError:
        year = _year(fact.information_as_of)
        return bool(year and year < generated_at.year - 2)
    return (generated_at.date() - published).days > 731


def build_overview(result: BriefingResult, *, company_limit: int = 8) -> list[OverviewItem]:
    """Build five distinct priorities; never mutate historical stored results."""
    facts = usable_facts(ResearchBundle(sources=result.sources, facts=result.facts), historical=True)
    company = [
        f for f in facts if f.subject_ref == "company" and f.status == "verified" and f.field in _LABELS
    ]
    company.sort(key=lambda f: list(_LABELS).index(f.field))
    company = company[:8]
    company = [
        f
        for f in company
        if not (
            f.field in ("business_model", "customers", "markets")
            and isinstance(f.value, str)
            and any(
                other.field == "services"
                and isinstance(other.value, str)
                and _text(f.value).casefold() in other.value.casefold()
                for other in company
            )
        )
    ]
    company = company[:company_limit]
    company_text = " ".join(f"{_LABELS[f.field]}: {_text(f.value).rstrip('.')}." for f in company)
    if company_text and not any(f.field in ("business_model", "services") for f in company):
        company_text = (
            "Geschäftsmodell und Leistungen sind noch nicht ausreichend belegt. Weitere belegte Merkmale: "
            + company_text
        )
    items = [
        _item(
            "company",
            company_text
            or "Geschäftsmodell und Leistungen der angefragten Einheit sind noch nicht ausreichend belegt.",
            company,
        )
    ]

    developments = [f for f in facts if f.subject_ref == "company" and f.field in DEVELOPMENT_FIELDS]
    developments.sort(key=lambda f: (_year(f.information_as_of), f.status == "verified"), reverse=True)
    development = developments[0] if developments else None
    if development:
        historical = _historical(development, result.generated_at)
        when = development.information_as_of or "Zeitbezug offen"
        prefix = "Historischer Kontext" if historical else "Belegte Entwicklung"
        items.append(
            _item(
                "development",
                f"{prefix} ({when}): {_text(development.value)}. Ein heutiger Bedarf ist damit nicht bestätigt.",
                [development],
            )
        )
    else:
        items.append(
            _item(
                "development",
                "Eine gesprächsrelevante Entwicklung mit belastbarem Zeitbezug wurde nicht belegt.",
            )
        )

    person_facts, person_text, person_sources = [], [], []
    for person in result.input.people:
        profile = next((p for p in result.people if p.participant_id == person.participant_id), None)
        roles = [
            f
            for f in facts
            if f.subject_ref == person.participant_id and f.field == "current_role" and f.status == "verified"
        ]
        if profile and profile.status == "confirmed" and roles:
            person_text.append(f"{person.name}: " + "; ".join(dict.fromkeys(_text(f.value) for f in roles)))
            person_facts.extend(roles)
        else:
            undated = [
                f
                for f in facts
                if f.subject_ref == person.participant_id
                and f.field == "undated_role"
                and f.status == "verified"
            ]
            context = [
                f
                for f in facts
                if f.subject_ref == person.participant_id
                and f.field in ("career", "historical_role", "professional_topic")
            ]
            if profile and profile.status == "ambiguous":
                person_text.append(f"{person.name}: mehrere berufliche Zuordnungen; vor Verwendung klären")
            elif undated:
                person_text.append(
                    f"{person.name}: {_text(undated[0].value)} (Rollenangabe belegt; Aktualität offen)"
                )
                person_facts.extend(undated[:1])
            elif context:
                person_text.append(
                    f"{person.name}: {_text(context[0].value)} (beruflicher Kontext; aktuelle Rolle offen)"
                )
                person_facts.extend(context[:1])
            else:
                candidates = [s for s in result.sources if person.participant_id in s.discovered_for]
                if candidates:
                    person_text.append(
                        f"{person.name}: Profil gefunden – Suchtreffer „{candidates[0].title}“. Zuordnung und Aktualität offen."
                    )
                    person_sources.extend(s.source_id for s in candidates)
                else:
                    person_text.append(
                        f"{person.name}: aktuelle Rolle beziehungsweise Identität nicht belastbar bestätigt"
                    )
            if context and undated:
                label = {
                    "historical_role": "Frühere Rolle",
                    "career": "Beruflicher Hintergrund",
                    "professional_topic": "Berufliches Thema",
                }[context[0].field]
                timing = f" ({context[0].information_as_of})" if context[0].information_as_of else ""
                person_text[-1] += f". {label}: " + _text(context[0].value) + timing
                person_facts.extend(context[:1])
    unresolved = any(
        any(
            marker in text
            for marker in (
                "nicht belastbar bestätigt",
                "Zuordnung und Aktualität offen",
                "mehrere berufliche Zuordnungen",
            )
        )
        for text in person_text
    )
    items.append(
        _item(
            "people",
            ". ".join(person_text) or "Keine Ansprechpartner angegeben.",
            person_facts,
            kind="unknown" if unresolved else None,
        )
    )
    items[-1].evidence_ids = list(dict.fromkeys([*items[-1].evidence_ids, *person_sources]))

    # Internal sales goals are instructions for ATP, never words to read to a prospect.
    opener = {
        "setup": "Ich würde gern zuerst Ihre Ausgangslage verstehen und anschließend gemeinsam klären, wo eine Zusammenarbeit sinnvoll wäre.",
        "audit_consultation": "Lassen Sie uns gemeinsam einordnen, wo im Arbeitsalltag Aufwand entsteht und welche Prozesse wir im Audit genauer untersuchen sollten.",
        "enablement_consultation": "Ich würde gern verstehen, wie Ihr Team KI heute nutzt und welche Unterstützung im Alltag am meisten helfen würde.",
        "project_review": "Lassen Sie uns auf den aktuellen Stand schauen: Was ist erreicht, was steht im Weg und was nehmen wir uns als Nächstes vor?",
    }.get(result.input.meeting_type, "Was wäre für Sie heute ein hilfreiches Ergebnis?")
    opener = "Gesprächsvorschlag: „" + opener + "“"
    items.append(_item("opener", opener, kind="hypothesis"))
    reason = result.recommendation.reason
    if result.recommendation.rule in ("R4", "qualification_missing"):
        reason = "Die Zielgruppenpassung ist noch offen. Größe und Führungsstruktur im Gespräch einordnen; fehlende öffentliche Daten sind kein Ausschluss."
    next_step = f"{result.recommendation.value}: {reason}"
    if result.quality.state == "clarification_required" and result.quality.reasons:
        next_step += " Vorab zu klären: " + result.quality.reasons[0]
    items.append(_item("next_step", next_step))
    return items


def presentation_overview(result: BriefingResult) -> list[OverviewItem]:
    """Repair the reader projection of old briefings without rewriting stored evidence."""
    fallback = build_overview(result, company_limit=3)
    authored = {item.category: item for item in result.overview}
    return [
        authored[item.category]
        if item.category in ("company", "development")
        and item.category in authored
        and len(authored[item.category].text) <= len(item.text)
        else item
        for item in fallback
        if item.category != "development"
        or (
            item.kind != "unknown"
            and any(fact.fact_id in item.fact_ids and fact.information_as_of for fact in result.facts)
        )
    ]


def reader_result(result: BriefingResult) -> BriefingResult:
    """Recover previously discarded profile links from recorded fetch attempts.

    This is a display-only copy: no network, new facts or persisted changes.
    The URL must already be recorded in the canonical run diagnostics.
    """
    from .models import ResearchQuery
    from .providers import _search_profiles

    view = result.model_copy(deep=True)
    urls = []
    for limit in result.limits:
        match = re.fullmatch(r"Beleg nicht nutzbar \([^)]+\): (https?://\S+)", limit)
        if match:
            urls.append({"url": match[1]})
    if urls and result.input.public_identifiers_confirmed:
        query = ResearchQuery.from_input(result.input)
        discoveries = _search_profiles(
            {"output": [{"type": "web_search_call", "status": "completed", "action": {"sources": urls}}]},
            query,
        )
        for source in discoveries:
            if not any(existing.url == source.url for existing in view.sources):
                source.source_id = "profile-" + sha256(source.url.encode()).hexdigest()[:16]
                source.retrieved_at = result.generated_at
                view.sources.append(source)
    return view


def attention_notes(result: BriefingResult) -> list[str]:
    """Only material conversation caveats belong in the default reading path."""
    notes = []
    ambiguous = [p.name for p in result.people if p.status == "ambiguous"]
    if ambiguous:
        notes.append("Personenzuordnung vor Verwendung klären: " + ", ".join(ambiguous) + ".")
    if not any(c.kind in ("fact", "user_confirmed") for c in result.company):
        notes.append(
            "Das Geschäftsmodell ist noch nicht ausreichend belegt; im Einstieg gemeinsam einordnen."
        )
    if result.icp.status == "deviating":
        notes.append(
            "Die Zielgruppenprüfung zeigt eine belegte Abweichung. Vor dem nächsten Vertriebschritt prüfen."
        )
    if result.quality.state == "clarification_required":
        notes.append(
            "Eine wesentliche Klärung ist vor der Verwendung offen. Details unter Recherchehinweise."
        )
    return notes
