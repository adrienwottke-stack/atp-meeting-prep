"""Readable report notes, derived without changing the stored research evidence."""

import re
from urllib.parse import urlsplit

QUALITY_LABELS = {
    "sufficient": "Für den bestätigten Anlass ausreichend belegt",
    "limited": "Mit Einschränkungen verwendbar",
    "clarification_required": "Wesentliche Klärung offen",
    "not_assessed": "Fachliche Verwendbarkeit nicht bewertet",
}
OVERVIEW_LABELS = {
    "company": "Unternehmen in 20 Sekunden",
    "development": "Für dieses Gespräch relevant",
    "people": "Dein Gegenüber",
    "opener": "Dein Gesprächseinstieg",
    "next_step": "Einordnung und nächster Schritt",
}


def quality_notes(result: dict) -> tuple[str, list[str]]:
    """Display persisted quality only; never infer qualification while rendering."""
    quality = result.get("quality") or {}
    return (
        QUALITY_LABELS.get(quality.get("state"), QUALITY_LABELS["not_assessed"]),
        list(quality.get("reasons") or []),
    )


def resolution_notes(result: dict) -> list[str]:
    """Keep deliberate unknowns separate from user-confirmed statements."""
    notes = []
    for key, value in (result.get("resolutions") or {}).items():
        if isinstance(value, str):
            notes.append(f"{key}: {value} / Historische Antwort; Herkunft nicht dokumentiert.")
            continue
        if not isinstance(value, dict):
            continue
        field = value.get("field") or key
        label = value.get("question") or FIELD_LABELS.get(field, field)
        if value.get("action") == "leave_unknown":
            text = "Bewusst ungeklärt gelassen; keine Tatsachenbestätigung."
        else:
            text = (
                str(value.get("value") or "Antwort hinterlegt")
                + " / Bestätigte Nutzereingabe; kein Web-Beleg."
            )
        provenance = []
        if value.get("confirmed_by"):
            provenance.append("Bestätigt von: " + str(value["confirmed_by"]))
        if value.get("confirmed_at"):
            provenance.append("Bestätigt am: " + str(value["confirmed_at"]))
        notes.append(str(label) + ": " + text + (" / " + " / ".join(provenance) if provenance else ""))
    return notes


FIELD_LABELS = {
    "business_model": "Geschäftsmodell",
    "services": "Leistungen",
    "industry": "Branche",
    "locations": "Standorte",
    "employees_total": "Gesamtmitarbeitende",
    "knowledge_workers": "Wissensarbeiter",
    "owner_or_family_led": "Eigentümer- oder Familienführung",
    "segment": "Mittelstandsbezug",
    "is_corporation": "Konzernzugehörigkeit",
    "is_public_authority": "Behörde",
    "current_role": "Berufliche Rolle",
    "historical_role": "Frühere Rolle",
    "undated_role": "Aktualität der Rolle",
    "career": "Beruflicher Werdegang",
    "professional_topic": "Berufliche Themen",
}


def report_notes(result: dict) -> dict[str, list[str]]:
    """Project raw diagnostics into human notes; preserve unknown/custom warnings."""
    raw_gaps = result.get("gaps", [])
    gaps, consumed = [], set()
    for person in result.get("people", []):
        name = person["name"]
        role_notes = {
            f"Für {name} enthalten die bereitgestellten Quellen keinen belegten beruflichen Eintrag.",
            f"{name}: aktuelle berufliche Rolle nicht eindeutig mit Zeitbezug bestätigt.",
            f"{name}: Aktuelle Rolle oder eindeutige Identität offen.",
        }
        details = []
        if role_notes.intersection(raw_gaps):
            details.append("aktuelle Rolle und Zuordnung zur angefragten Firma nicht ausreichend belegt")
            consumed.update(role_notes)
        for suffix, display in (
            (
                "Beruflicher Werdegang nicht ausreichend belegt.",
                "beruflicher Werdegang nicht ausreichend belegt",
            ),
            (
                "Relevante berufliche Themen nicht ausreichend belegt.",
                "berufliche Themen nicht ausreichend belegt",
            ),
        ):
            raw = f"{name}: {suffix}"
            if raw in raw_gaps:
                details.append(display)
                consumed.add(raw)
        if details:
            gaps.append(f"{name}: " + "; ".join(details) + ".")

    criteria = {c["field"]: c for c in result.get("icp", {}).get("criteria", [])}
    rejection = re.compile(
        r"^(\w+): (?:Aussagewert wird von der Fundstelle nicht konkret gestützt\.|"
        r"kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen\.|"
        r"Bezug auf die angefragte Unternehmenseinheit nicht belegt\.|"
        r"unbekannte Subjektkennung; Fakt nicht übernommen\.)$"
    )
    for gap in raw_gaps:
        if gap in consumed:
            continue
        match = rejection.match(gap)
        if match and match[1] in FIELD_LABELS:
            field, label = match[1], FIELD_LABELS[match[1]]
            # A rejected candidate is not an additional gap if the final ICP
            # criterion already explains this field (including conflicts).
            criterion = criteria.get(field)
            if criterion and (
                criterion.get("status") in ("matching", "deviating")
                or any(g.startswith(label + ":") for g in raw_gaps)
            ):
                continue
            gap = label + ": Keine ausreichend belegte Angabe zur angefragten Einheit verfügbar."
        gaps.append(gap)

    limits = []
    for limit in result.get("limits", []):
        match = re.fullmatch(r"Beleg nicht nutzbar \(([^)]+)\): (https?://\S+)", limit)
        if not match:
            limits.append(limit)
            continue
        code, url = match.groups()
        try:
            host = (urlsplit(url).hostname or "Quelle").lower()
        except ValueError:
            limits.append("Eine gefundene Quelle konnte nicht sicher abgerufen werden.")
            continue
        linkedin = host == "linkedin.com" or host.endswith(".linkedin.com")
        title = "LinkedIn" if linkedin else host.removeprefix("www.")
        if code in ("evidence_http_403", "evidence_http_429", "evidence_http_999"):
            explanation = "Automatischer Quellenabruf gesperrt oder begrenzt; die betroffenen Inhalte wurden nicht als Beleg verwendet."
        elif code == "evidence_timeout":
            explanation = "Quelle innerhalb der verfügbaren Abrufzeit nicht erreichbar."
        elif code in ("evidence_content_type_blocked", "evidence_encoding_blocked", "evidence_size_limit"):
            explanation = "Quellenformat oder Umfang konnte in diesem Lauf nicht verarbeitet werden."
        else:
            explanation = "Quelle konnte in diesem Lauf nicht als Beleg abgerufen werden."
        limits.append(f"{title}: {explanation}")
    return {
        "gaps": list(dict.fromkeys(gaps)),
        "limits": list(dict.fromkeys(limits)),
        "resolutions": resolution_notes(result),
    }
