"""Versioned, deterministic clarification, ICP and meeting recommendation rules."""

from __future__ import annotations

import ipaddress
import math
import re
import unicodedata
from urllib.parse import urlsplit

from .models import (
    Clarification,
    Criterion,
    Fact,
    ICPResult,
    MeetingInput,
    MeetingType,
    Recommendation,
    RecommendationValue,
    Relationship,
)

RULE_VERSION = "atp-rules-2"
ICP_FIELDS = ("owner_or_family_led", "segment", "employees_total", "is_corporation", "is_public_authority")
LABELS = {
    "owner_or_family_led": "Eigentümer- oder Familienführung",
    "segment": "Mittelstandsbezug",
    "employees_total": "Gesamtmitarbeitende der angefragten Einheit",
    "is_corporation": "Konzernzugehörigkeit",
    "is_public_authority": "Behörde",
}


def _q(code, field, reason, options, handling, participant_id=None):
    # Stable identifiers survive checkpoints and repeated rendering.
    return Clarification(
        question_id=f"{code}:{participant_id or field}",
        code=code,
        field=field,
        reason=reason,
        options=options,
        handling=handling,
        participant_id=participant_id,
    )


def _norm(value):
    return re.sub(
        r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    )


def valid_website(website: str) -> bool:
    try:
        parsed = urlsplit(website)
        host = (parsed.hostname or "").rstrip(".").lower()
        if (
            host == "localhost"
            or host == "metadata.google.internal"
            or host.endswith((".localhost", ".local", ".internal", ".home"))
        ):
            return False
        try:
            if not ipaddress.ip_address(host).is_global:
                return False
        except ValueError:
            pass
        return (
            parsed.scheme in ("http", "https")
            and bool(host and "." in host)
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 80, 443)
            and not any(ord(c) < 33 for c in website)
            and "\\" not in website
        )
    except ValueError:
        return False


def clarify_input(input: MeetingInput) -> list[Clarification]:
    questions = []
    core = []
    if not input.company.name.strip():
        core.append(("company.name", "Der Firmenname fehlt."))
    if not valid_website(input.company.website):
        core.append(("company.website", "Eine gültige öffentliche HTTP(S)-Website wird benötigt."))
    if not input.people or not any(p.name.strip() for p in input.people):
        core.append(("people", "Mindestens ein Ansprechpartner mit Namen wird benötigt."))
    for p in input.people:
        if not p.name.strip():
            core.append((f"people.{p.participant_id}.name", "Der Name dieser Person fehlt."))
    if input.meeting_type is None:
        core.append(("meeting_type", "Der bestätigte Meeting-Anlass fehlt."))
    for field, reason in core:
        questions.append(_q("K01", field, reason, ["Angabe ergänzen oder korrigieren"], "blocking_input"))
    if core:
        return questions
    # A lexical mismatch is a request to confirm, never a different-company assertion.
    # An explicit entity_hint records the user's confirmation and permits brand domains.
    base = re.sub(r"\b(gmbh|co|kg|ag|se|ohg|ug|ltd|inc|mbh|und|the)\b", " ", input.company.name.lower())
    tokens = [_norm(t) for t in base.split() if len(_norm(t)) >= 3]
    domain = _norm(urlsplit(input.company.website).hostname)
    acronym = _norm("".join(t[0] for t in base.split() if t))
    if (
        tokens
        and not any(t in domain for t in tokens)
        and not (len(acronym) >= 2 and acronym in domain)
        and not input.company.entity_hint.strip()
    ):
        questions.append(
            _q(
                "K02",
                "company.entity_hint",
                "Firmenname und Domain sind nicht eindeutig zuzuordnen. Bitte die angefragte Unternehmenseinheit bestätigen; Marken-Domains können korrekt sein.",
                ["Firma und Website gehören zusammen", "Firmenname oder Website korrigieren"],
                "blocking_company",
            )
        )
    if input.meeting_type == MeetingType.PROJECT and input.relationship == Relationship.NEW:
        questions.append(
            _q(
                "K04",
                "relationship",
                "Projektbesprechung und Neukontakt passen ohne weiteren Kontext nicht eindeutig zusammen.",
                ["Laufendes Projekt", "Bestandskunde", "Neukontakt; Anlass korrigieren"],
                "blocking_dependent_output",
            )
        )
    if input.relationship in (Relationship.PROJECT, Relationship.EXISTING) and not input.goal.strip():
        questions.append(
            _q(
                "K04",
                "goal",
                "Für die bestehende Beziehung fehlt das konkrete Gesprächsziel; Firmenrecherche ist davon unabhängig möglich.",
                ["Gesprächsziel ergänzen"],
                "blocking_dependent_output",
            )
        )
    return questions


def _eligible(fact: Fact) -> bool:
    if fact.subject_ref != "company" or fact.status != "verified":
        return False
    return (fact.provenance == "web_verified" and bool(fact.evidence_ids)) or (
        fact.provenance == "user_confirmed" and bool(fact.confirmed_by and fact.confirmed_at)
    )


def _facts_by_field(facts):
    grouped = {}
    for raw in facts:
        fact = raw if isinstance(raw, Fact) else Fact.model_validate(raw)
        grouped.setdefault(fact.field, []).append(fact)
    return grouped


def _leadership_from_components(grouped):
    """Derive owner-led OR family-led without inferring leadership from ownership.

    One proven true branch establishes the disjunction. False requires two
    explicit false branches; one false branch plus unknown remains unknown.
    Conflicting combined/component facts are handled by normal criterion logic.
    """
    branches = []
    for field in ("owner_led", "family_led"):
        candidates = grouped.get(field, [])
        usable = [f for f in candidates if _eligible(f) and isinstance(f.value, bool)]
        values = {f.value for f in usable}
        conflict = any(f.status == "conflicting" for f in candidates) or len(values) > 1
        branches.append((usable if len(values) == 1 and not conflict else [], conflict))
    decisive = next((fs for fs, _ in branches if fs and fs[0].value is True), None)
    if decisive:
        support = [decisive[0]]
    elif all(fs and fs[0].value is False for fs, _ in branches):
        support = [fs[0] for fs, _ in branches]
    elif any(conflict for _, conflict in branches):
        return Fact(
            field="owner_or_family_led",
            status="conflicting",
            conflict="Widersprüchliche operative Führungsangaben.",
        )
    else:
        return None
    # A mixture including user confirmation must not acquire web-only provenance.
    origin = next((f for f in support if f.provenance == "user_confirmed"), support[0])
    return origin.model_copy(
        update={
            "field": "owner_or_family_led",
            "value": support[0].value,
            "evidence_ids": list(dict.fromkeys(e for f in support for e in f.evidence_ids)),
        }
    )


def _number_range(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return (value, value)
    if isinstance(value, dict):
        low, high = value.get("min"), value.get("max")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        low, high = value
    else:
        return None
    if (
        isinstance(low, bool)
        or isinstance(high, bool)
        or not isinstance(low, (int, float))
        or not isinstance(high, (int, float))
        or not math.isfinite(low)
        or not math.isfinite(high)
        or low < 0
        or low > high
    ):
        return None
    return (low, high)


def evaluate_icp(facts, input: MeetingInput, profile: dict) -> ICPResult:
    grouped = _facts_by_field(facts)
    leadership = _leadership_from_components(grouped)
    if leadership is not None:
        grouped.setdefault("owner_or_family_led", []).append(leadership)
    criteria = []
    corridor = profile["icp"]["employees_total"]
    for field in ICP_FIELDS:
        candidates = grouped.get(field, [])
        usable = [f for f in candidates if _eligible(f)]
        values = {repr(f.value) for f in usable}
        contradictory = any(f.status == "conflicting" for f in candidates) or len(values) > 1
        fact = usable[0] if len(values) == 1 and not contradictory else None
        status, deviation, reason = (
            "unclear",
            None,
            "Kein eindeutiger belegter Nachweis für die angefragte Unternehmenseinheit.",
        )
        if contradictory:
            reason = "Widersprüchliche Angaben; keine automatische Auswahl einer Quelle."
        if fact:
            value = fact.value
            if field == "employees_total":
                span = _number_range(value)
                if span is None:
                    reason = "Belegschaftszahl nicht eindeutig interpretierbar."
                elif span[0] < corridor["min"] <= span[1] or span[0] <= corridor["max"] < span[1]:
                    reason = "Die belegte Spanne überschreitet eine Korridorgrenze; genaue Größe ungeklärt."
                elif span[1] < corridor["min"] or span[0] > corridor["max"]:
                    status, deviation = "deviating", "corridor"
                    reason = f"Außerhalb des veröffentlichten Zielkorridors {corridor['min']} bis {corridor['max']}; kein harter Ausschluss."
                else:
                    status, reason = (
                        "matching",
                        "Innerhalb des einschließlich der Grenzen geltenden Zielkorridors.",
                    )
            elif field in ("is_corporation", "is_public_authority"):
                if value is True:
                    status, deviation, reason = (
                        "deviating",
                        "exclusion",
                        "Belegter Ausschluss für die Neukontakt-Qualifizierung.",
                    )
                elif value is False:
                    status, reason = "matching", "Ausschluss durch den vorliegenden Nachweis verneint."
            elif field == "owner_or_family_led":
                if value is True:
                    status, reason = "matching", "Eigentümer- oder Familienführung ausdrücklich belegt."
                elif value is False:
                    status, deviation, reason = (
                        "deviating",
                        "target_structure",
                        "Belegte Führungsstruktur weicht vom Zielprofil ab.",
                    )
                else:
                    reason = "Eine Geschäftsführerrolle oder Namensgleichheit belegt keine Eigentümerführung."
            elif field == "segment":
                if isinstance(value, str) and _norm(value) == _norm(profile["icp"]["segment"]):
                    status, reason = "matching", "Mittelstandsbezug ausdrücklich belegt."
                elif value is False or (
                    isinstance(value, str)
                    and _norm(value) in ("keinmittelstand", "nichtmittelstand", "konzern", "behoerde")
                ):
                    status, deviation, reason = (
                        "deviating",
                        "target_structure",
                        "Belegter fehlender Mittelstandsbezug.",
                    )
        criteria.append(
            Criterion(
                field=field,
                label=LABELS[field],
                value=fact.value if fact else None,
                status=status,
                evidence_ids=fact.evidence_ids if fact else [],
                provenance=fact.provenance if fact else "unknown",
                information_as_of=fact.information_as_of if fact else None,
                deviation_kind=deviation,
                reason=reason,
            )
        )
    deviations = sorted({c.deviation_kind for c in criteria if c.deviation_kind})
    status = (
        "deviating"
        if deviations
        else "unclear"
        if any(c.status == "unclear" for c in criteria)
        else "matching"
    )
    open_points = [f"{c.label}: {c.reason}" for c in criteria if c.status == "unclear"]
    workers = [f for f in grouped.get("knowledge_workers", []) if _eligible(f)]
    span = _number_range(workers[0].value) if workers and len({repr(f.value) for f in workers}) == 1 else None
    threshold = profile["routing"]["knowledge_workers_approx_threshold"]
    if input.meeting_type == MeetingType.PROJECT:
        services, entry_reason = (
            ["enablement"],
            "Projektfortschritt, Hindernisse und Übergabe orientieren den Einstieg; keine neue Qualifizierung.",
        )
    elif input.meeting_type == MeetingType.ENABLEMENT:
        services, entry_reason = (
            ["enablement"],
            "Der bestätigte Enablement-Anlass begründet den Leistungseinstieg.",
        )
    elif input.meeting_type == MeetingType.AUDIT:
        services, entry_reason = (
            ["audit"],
            "Der bestätigte Audit-Anlass orientiert den Einstieg; Umfang im Gespräch prüfen.",
        )
    elif span and span[1] < threshold:
        services, entry_reason = (
            ["training", "workshop"],
            "Unter rund 30 belegten Wissensarbeitern: Schulung oder Workshop als Einstieg prüfen.",
        )
    elif span and span[0] >= threshold:
        services, entry_reason = (
            ["audit"],
            "Rund 30 oder mehr belegte Wissensarbeiter: AI Audit als möglichen Einstieg prüfen.",
        )
    else:
        services, entry_reason = (
            ["entry"],
            "Zahl der Wissensarbeiter klären, um Audit gegenüber Schulung oder Workshop abzuwägen; kein ICP-Grenzwert.",
        )
    if span is None:
        open_points.append(
            "Leistungseinstieg: Wie viele Wissensarbeiter sollen einbezogen werden? Gesamtbelegschaft ist kein Ersatz."
        )
    allowed = {s["id"] for s in profile["services"]}
    return ICPResult(
        status=status,
        criteria=criteria,
        deviation_kinds=deviations,
        entry_service_ids=[s for s in services if s in allowed],
        entry_reason=entry_reason,
        open_points=open_points,
    )


def recommend(input: MeetingInput, icp: ICPResult, *, company_clear=True, goal_clear=True) -> Recommendation:
    def result(value, reason, rule):
        return Recommendation(value=value, reason=reason, rule=rule)

    if not company_clear or not goal_clear:
        return result(
            RecommendationValue.CLARIFY,
            "Unternehmenszuordnung oder konkretes Gesprächsziel vor der abhängigen Entscheidung klären.",
            "R1",
        )
    if input.relationship == Relationship.PROJECT:
        if not input.goal.strip():
            return result(
                RecommendationValue.CLARIFY,
                "Das konkrete Projektziel fehlt; bestehende Verpflichtungen und den gewünschten Fortschritt klären.",
                "R1-PROJECT-GOAL",
            )
        return result(
            RecommendationValue.ATTEND,
            "Laufendes Projekt: bestätigtes Ziel bearbeiten. Der ICP-Befund bleibt Kontext und löst keine Ablehnung aus.",
            "R2",
        )
    deviations = set(icp.deviation_kinds)
    if input.relationship == Relationship.UNKNOWN and (deviations or icp.status == "unclear"):
        return result(
            RecommendationValue.CLARIFY,
            "Die unbekannte Kundenbeziehung kann die Empfehlung ändern: Neukontakt, Bestandskunde oder laufendes Projekt bestätigen (K04).",
            "R1-K04",
        )
    if input.relationship == Relationship.NEW and "exclusion" in deviations:
        if input.exception_reason.strip():
            return result(
                RecommendationValue.EXCEPTION,
                "Bestätigter Ausschluss beim Neukontakt; dokumentierten Ausnahmegrund fachlich prüfen: "
                + input.exception_reason,
                "R3-EXCEPTION",
            )
        return result(
            RecommendationValue.DECLINE,
            "Konzern- oder Behördenzugehörigkeit ist als Ausschluss für den Neukontakt belegt. ATP entscheidet über Absage oder Weiterleitung; keine automatische Aktion.",
            "R3",
        )
    new_sales = input.relationship == Relationship.NEW or (
        input.relationship == Relationship.EXISTING and input.relationship_context == "new_sales_inquiry"
    )
    if (
        input.relationship == Relationship.EXISTING
        and input.relationship_context == "unspecified"
        and (deviations or icp.status == "unclear")
    ):
        return result(
            RecommendationValue.CLARIFY,
            "Bestandskunde: bestehende Verpflichtung und neue Vertriebsanfrage unterscheiden, weil der ICP-Befund die Empfehlung ändern kann (K04).",
            "R1-EXISTING-CONTEXT",
        )
    if new_sales and "exclusion" in deviations:
        if input.exception_reason.strip():
            return result(
                RecommendationValue.EXCEPTION,
                "Bestätigter Ausschluss bei neuer Vertriebsanfrage; dokumentierten Ausnahmegrund prüfen: "
                + input.exception_reason,
                "R3-EXCEPTION",
            )
        return result(
            RecommendationValue.DECLINE,
            "Neue Vertriebsanfrage mit belegtem Konzern-/Behördenausschluss; ATP entscheidet über Absage oder Weiterleitung.",
            "R3",
        )
    if input.relationship == Relationship.EXISTING and input.relationship_context != "new_sales_inquiry":
        if not input.goal.strip():
            return result(
                RecommendationValue.CLARIFY,
                "Bestandskunde: Anlass und Ziel gegenüber bestehenden Verpflichtungen oder einer neuen Vertriebsanfrage konkretisieren.",
                "R1-EXISTING-GOAL",
            )
        return result(
            RecommendationValue.ATTEND,
            "Bestandsbeziehung und bestätigten Anlass berücksichtigen; ICP-Abweichungen allein begründen keine Absage.",
            "R2-EXISTING",
        )
    if any(c.status == "unclear" for c in icp.criteria):
        return result(
            RecommendationValue.CLARIFY,
            "Entscheidungsrelevante Qualifizierungsbelege fehlen oder widersprechen sich (K06). Vorab klären; keine negative Qualifizierung aus fehlenden Daten.",
            "R4",
        )
    if deviations:
        return result(
            RecommendationValue.EXCEPTION,
            "Belegte Abweichung vom Zielprofil; Gesprächsnutzen und begründete Ausnahme prüfen. Eine Größenabweichung ist kein harter Ausschluss.",
            "R5",
        )
    return result(
        RecommendationValue.ATTEND,
        "Zielprofil passt. Bestätigten Anlass aufnehmen und verbleibende Recherche- oder Einstiegsfragen im Gespräch klären.",
        "R6",
    )


def research_clarifications(input, bundle, icp):
    # Legacy entrypoint delegates to the same decision used by the new workflow.
    from .clarification import plan_clarifications

    return plan_clarifications(input, bundle, icp).open_questions


def clarification_for_crm(candidates):
    if len(candidates) < 2:
        return []
    return [
        _q(
            "K05",
            "target_record_id",
            "Mehrere CRM-Ziele sind plausibel; nur die Übertragung wartet auf Bestätigung.",
            [str(c) for c in candidates] + ["Übertragung nicht ausführen"],
            "blocking_transfer",
        )
    ]


def apply_research_answers(bundle, input, answers):
    # Kept here as the workflow's domain entrypoint; implementation only manipulates
    # validated facts and does not invoke provider methods or network access.
    from .providers import apply_research_answers as apply

    return apply(bundle, input, answers)
