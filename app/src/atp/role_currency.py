"""Conservative currency gate for publicly evidenced current person roles.

A date at most 180 days old is necessary, never proof against later cessation
evidence. Publication/access dates, year/month-only dates and inferred dates do
not establish current employment. Semantic subject/cessation review remains an
independent requirement before a role can be used as current.
"""

from __future__ import annotations

import re
from datetime import date

from .models import ResearchBundle

_MONTHS = {
    "january": 1,
    "januar": 1,
    "jan": 1,
    "february": 2,
    "februar": 2,
    "feb": 2,
    "march": 3,
    "märz": 3,
    "maerz": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "mai": 5,
    "june": 6,
    "juni": 6,
    "jun": 6,
    "july": 7,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oktober": 10,
    "oct": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dezember": 12,
    "dec": 12,
    "dez": 12,
}
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_PATTERNS = [
    re.compile(r"(?<!\w)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\w)"),
    re.compile(r"(?<!\w)(?P<day>\d{1,2})\.\s*(?P<month>\d{1,2})\.\s*(?P<year>\d{4})(?!\w)"),
    re.compile(r"\b(?P<day>\d{1,2})\.?\s+(?P<month>" + _MONTH + r")\.?\s+(?P<year>\d{4})\b", re.I),
    re.compile(r"\b(?P<month>" + _MONTH + r")\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})\b", re.I),
]
_PUBLICATION = re.compile(
    r"\b(?:published|publication|veröffentlicht|publiziert|abgerufen|abrufdatum|retrieved|accessed|downloaded)\b",
    re.I,
)
# Academic titles inside a dated speaker heading are not sentence boundaries.
# Keep this list narrow: ordinary full stops must still separate unrelated dates.
_ACADEMIC_TITLE = re.compile(r"\b(?:Dr|Prof)\.(?=\s+[A-ZÄÖÜ])")


def _parse(match):
    month = match.group("month").casefold()
    number = int(month) if month.isdigit() else _MONTHS[month]
    try:
        return date(int(match.group("year")), number, int(match.group("day")))
    except ValueError:
        return None


def _full_date(value):
    for pattern in _PATTERNS:
        match = pattern.fullmatch((value or "").strip())
        if match:
            return _parse(match)
    return None


def _has_matching_role_date(quote, value, expected):
    if not isinstance(value, str):
        return False
    words = re.findall(r"[^\W_]+", value, re.UNICODE)
    if not words:
        return False
    role_pattern = re.compile(r"\b" + r"[\W_]+".join(re.escape(w) for w in words) + r"\b", re.I)
    for pattern in _PATTERNS:
        for matched_date in pattern.finditer(quote):
            if _parse(matched_date) != expected:
                continue
            # Explicit page publication/access metadata is not a role's date.
            prefix = re.split(r"[.!?;\n]", quote[max(0, matched_date.start() - 65) : matched_date.start()])[
                -1
            ]
            if _PUBLICATION.search(prefix):
                continue
            for role in role_pattern.finditer(quote):
                start, end = min(role.start(), matched_date.start()), max(role.end(), matched_date.end())
                if end - start > 220:
                    continue
                between = (
                    quote[role.end() : matched_date.start()]
                    if role.end() <= matched_date.start()
                    else quote[matched_date.end() : role.start()]
                )
                if not re.search(r"[;!?]|\.(?=\s|$)", _ACADEMIC_TITLE.sub("", between)):
                    return True
    return False


def enforce_current_role_currency(
    bundle: ResearchBundle,
    evaluation_date: date,
    max_age_days: int = 180,
) -> ResearchBundle:
    """Downgrade insufficiently dated web roles to undated_role on a deep copy.

    Only verified web_verified current_role facts are gated. Historical records
    and explicit user confirmation retain their existing provenance/status.
    A fully quoted matching role date must be nonfuture and at most 180 days old
    by default; no full date is inferred from a year, month, or retrieval time.
    """
    if not isinstance(evaluation_date, date):
        raise TypeError("evaluation_date must be a date")
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 0:
        raise ValueError("max_age_days must be a nonnegative integer")
    result = bundle.model_copy(deep=True)
    for fact in result.facts:
        if fact.field != "current_role" or fact.status != "verified" or fact.provenance != "web_verified":
            continue
        as_of = _full_date(fact.information_as_of)
        age = (evaluation_date - as_of).days if as_of else None
        quote = fact.supporting_quote.strip()
        if as_of and 0 <= age <= max_age_days and quote and _has_matching_role_date(quote, fact.value, as_of):
            continue
        reason = (
            "kein vollständiges eindeutiges Datum im Informationsstand"
            if as_of is None
            else "Informationsstand liegt nach dem Bewertungsdatum"
            if age < 0
            else f"Rollenbeleg älter als {max_age_days} Tage"
            if age > max_age_days
            else "vollständiges Datum und konkrete Rolle nicht gemeinsam im Belegzitat bestätigt"
        )
        fact.field = "undated_role"
        result.gaps.append(
            f"{fact.subject_ref}: aktuelle berufliche Rolle nicht ausreichend aktuell belegt ({reason}); "
            f"belegte Rollenangabe mit Informationsstand {fact.information_as_of or 'unbekannt'} bleibt als Kontext erhalten."
        )
    result.gaps = list(dict.fromkeys(result.gaps))
    return result
