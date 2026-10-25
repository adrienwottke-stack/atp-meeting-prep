"""Reconcile extraction-wave gaps against the final, supported evidence state.

This is presentation cleanup, never evidence promotion. Unknown text that cannot
be attributed to a precise subject/field is retained rather than guessed away.
"""

from __future__ import annotations

import json
import re

from .models import MeetingInput, ResearchBundle
from .research_coverage import usable_facts

_CONFLICT = re.compile(r"widerspr|konflikt|conflict|contradict|mehrdeutig|ambig", re.I)
_TECHNICAL = re.compile(
    r"\b(?:http[_\s-]*[345]\d\d|evidence_|timeout|dns|budget|zeitlimit|technisch|zugriffssperre)", re.I
)
_MISSING = re.compile(
    r"kein|nicht|unbelegt|ungeklärt|unbekannt|fehlt|fehlend|unknown|missing|unverified|not supported", re.I
)
_PREFIX = re.compile(r"^([a-z][a-z0-9_]*):\s*(.*)$", re.S)
_PERSON_FIELDS = {"current_role", "historical_role", "undated_role", "career", "professional_topic"}


def _person_fields(text):
    value = text.casefold()
    fields = set()
    if re.search(
        r"aktuell\w* (?:beruflich\w* )?rolle|aktuelle\w* zuordnung|current.role|aktuelle rolle", value
    ):
        fields.add("current_role")
    elif "rolle" in value or "role" in value:
        fields.add("current_role")
    if re.search(r"werdegang|karriere|career|beruflich\w* hintergrund", value):
        fields.add("career")
    if re.search(r"themen|professional.topic", value):
        fields.add("professional_topic")
    return fields


def reconcile_gaps(bundle: ResearchBundle, input: MeetingInput) -> ResearchBundle:
    result = bundle.model_copy(deep=True)
    facts = usable_facts(result)
    historical = usable_facts(result, historical=True)
    ambiguous = {q.participant_id for q in result.clarifications if q.code == "K03"}
    rejected = {r.fact_id for r in result.evidence_review if r.verdict != "supported"}

    def ready(subject, field, *, allow_historical=False):
        if subject in ambiguous:
            return False
        candidates = [f for f in result.facts if f.subject_ref == subject and f.field == field]
        if any(f.status == "conflicting" or f.conflict for f in candidates):
            return False
        pool = historical if allow_historical else facts
        accepted = [
            f for f in pool if f.subject_ref == subject and f.field == field and f.fact_id not in rejected
        ]
        if not accepted:
            return False
        if field == "current_role" and not all(f.information_as_of for f in accepted):
            return False
        return len({json.dumps(f.value, sort_keys=True, ensure_ascii=False) for f in accepted}) == 1

    def person_ready(person, field):
        if field == "career":
            return ready(person.participant_id, "career", allow_historical=True) or ready(
                person.participant_id, "historical_role", allow_historical=True
            )
        return ready(person.participant_id, field)

    gaps = []
    for gap in result.gaps:
        if _CONFLICT.search(gap) or _TECHNICAL.search(gap) or not _MISSING.search(gap):
            gaps.append(gap)
            continue
        prefix = _PREFIX.match(gap)
        if prefix:
            field = prefix.group(1)
            if field in _PERSON_FIELDS:
                # An unscoped person gap cannot be cleared by evidence for just
                # one of several requested people.
                if input.people and all(person_ready(p, field) for p in input.people):
                    continue
            elif ready("company", field):
                continue
        mentioned = [p for p in input.people if p.name.strip() and p.name.casefold() in gap.casefold()]
        fields = _person_fields(gap)
        # Never erase a combined-role-and-other-information gap from role alone.
        broad = bool(re.search(r"weiter\w* beruflich\w* merkmal|other professional", gap, re.I))
        if (
            mentioned
            and fields
            and not broad
            and all(person_ready(p, field) for p in mentioned for field in fields)
        ):
            continue
        # This exact broad gap was observed in the public provider series. Split
        # it rather than imply that a markets fact also established customers.
        if re.search(r"belege zu kunden oder märkten", gap, re.I):
            customers, markets = ready("company", "customers"), ready("company", "markets")
            if customers and markets:
                continue
            if customers or markets:
                gaps.append(
                    "Märkte: keine ausreichend belegten Angaben."
                    if customers
                    else "Kunden: keine ausreichend belegten Angaben."
                )
                continue
        gaps.append(gap)

    # Missing input people remain explicit even if extraction forgot to report
    # them. Background and topics remain distinct from the current-role check.
    for person in input.people:
        for field, description in (
            ("current_role", "aktuelle berufliche Rolle nicht eindeutig mit Zeitbezug bestätigt"),
            ("career", "beruflicher Werdegang nicht ausreichend belegt"),
            ("professional_topic", "relevante berufliche Themen nicht ausreichend belegt"),
        ):
            existing = any(
                person.name.casefold() in gap.casefold() and field in _person_fields(gap) for gap in gaps
            )
            if not person_ready(person, field) and not existing:
                gaps.append(f"{person.name}: {description}.")
    result.gaps = list(dict.fromkeys(gaps))
    return result
