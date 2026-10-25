"""Conservative checks shared by live and controlled research paths.

These checks do not turn user input into public facts and never perform I/O.
Semantic model review remains an additional, fail-closed gate.
"""

from __future__ import annotations

import re

from .models import Clarification, InputAssessment


def website_boilerplate(value):
    """Website operation/terms are not a company's market-facing services."""
    if isinstance(value, list):
        return any(website_boilerplate(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(re.search(
        r"\boperat\w*\b.{0,35}\b(?:this|our)\s+website\b"
        r"|\bbetreib\w*\b.{0,35}\b(?:diese\w*|unsere\w*)\s+(?:webseite|website|internetseite|internetauftritt)\b"
        r"|\b(?:terms of use|conditions of use|nutzungsbedingungen|haftungsausschluss)\b",
        value, re.I,
    ))


def negated_value(value, excerpt):
    """Reject a value explicitly negated in its own clause (German/English).

    Do not use absence of these patterns as semantic proof. In particular, a
    negation elsewhere in the paragraph must not invalidate an unrelated fact.
    """
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str) or not item.strip():
            continue
        escaped = re.escape(item.strip())
        before = r"\b(?:nicht|keine?[nmr]?|weder|not|no|never)\b(?:\s+[\w-]+){0,4}\s*$"
        after = r"\s+(?:ist|sind|is|are)\s+(?:nicht|not|no)\b"
        for match in re.finditer(escaped, excerpt, re.I):
            prefix = excerpt[max(0, match.start() - 100) : match.start()]
            prefix = re.split(r"[.;!?]|\b(?:aber|but|sondern|however|und|and)\b", prefix, flags=re.I)[-1]
            if re.search(before, prefix, re.I) or re.match(after, excerpt[match.end() :], re.I):
                return True
    return False


def boolean_verdict(field, value, excerpt):
    """Tri-state semantics: absence of a positive assertion is never false.

    Ownership and operational leadership are different. For owner OR family led,
    false needs explicit denial of both forms of leadership.
    """
    owner = r"(?:eigentümer|inhaber)geführt\w*|owner[- ](?:managed|led)|led by (?:its |the )?owners?|(?:eigentümer|inhaber)\w*\s+(?:führt|führen|leitet|leiten)"
    family = r"familiengeführt\w*|family[- ](?:run|led)|geführt (?:von|durch) (?:die )?familie|familie\s+(?:führt|leitet)|managed by (?:the )?family"
    terms = {
        "owner_or_family_led": (owner, family),
        "is_corporation": (
            r"konzern\w*|tochtergesellschaft\w*|tochterunternehmen\w*|subsidiar\w*|part of (?:a |the )?(?:corporate )?group",
        ),
        "is_public_authority": (r"behörde\w*|public authority|bundesamt|ministerium",),
        "owner_led": (owner,),
        "family_led": (family,),
        "family_ownership": (
            r"familienbesitz|familieneigentum|im besitz (?:der|einer) familie|family[- ]owned|owned by (?:the |a )?family",
        ),
    }
    patterns = terms.get(field)
    if not patterns:
        return "unclear", "Kein feldspezifischer boolescher Nachweis."
    positive, negative = [], []
    for pattern in patterns:
        matches = list(re.finditer(r"\b(?:" + pattern + r")\b", excerpt, re.I))
        pos = neg = False
        for match in matches:
            prefix = excerpt[max(0, match.start() - 55) : match.start()]
            prefix = re.split(r"[.;!?]|\b(?:aber|but|jedoch|however|und|and)\b", prefix, flags=re.I)[-1]
            is_neg = bool(
                re.search(
                    r"\b(?:nicht|keine?[nmr]?|keinem|weder|not|no|neither)(?:\s+[\w-]+){0,3}\s*$",
                    prefix,
                    re.I,
                )
            )
            is_neg |= bool(re.match(r"\s+(?:nicht|not)\b", excerpt[match.end() :], re.I))
            if field == "is_corporation" and re.fullmatch(r"konzernunabhängig\w*", match.group(), re.I):
                is_neg = True
            neg |= is_neg
            pos |= not is_neg
        positive.append(pos)
        negative.append(neg)
    if field == "is_corporation" and re.search(
        r"\b(?:konzernunabhängig|independent of corporate groups|not part of a group)\b", excerpt, re.I
    ):
        negative[0] = True
        # The 'konzern...' occurrence was an explicit negative assertion.
        if (
            re.fullmatch(r".*\bkonzernunabhängig\w*\b.*", excerpt, re.I | re.S)
            and len(re.findall(r"\bkonzern\w*\b", excerpt, re.I)) == 1
        ):
            positive[0] = False
    if field == "is_corporation":
        # Denying subsidiary status does not rule out being the parent/group.
        group_denial = bool(
            re.search(
                r"\b(?:kein(?:em|en)? konzern|nicht (?:teil|bestandteil) (?:eines|einem) konzern|konzernunabhängig|independent of corporate groups|not part of (?:a |the )?group)\b",
                excerpt,
                re.I,
            )
        )
        if not value and not group_denial:
            return (
                ("contradicted", "Konzernbezug wird ausdrücklich genannt.")
                if any(positive)
                else (
                    "unclear",
                    "Keine Tochtergesellschaft belegt für sich keine vollständige Konzernunabhängigkeit.",
                )
            )
    if field == "owner_or_family_led" and re.search(
        r"\b(?:weder eigentümer- noch familiengeführt|neither owner[- ]managed nor family[- ](?:run|led))\b",
        excerpt,
        re.I,
    ):
        negative = [True, True]
    if any(p and n for p, n in zip(positive, negative, strict=True)):
        return (
            "unclear",
            "Positive und negative Aussagen zum gleichen Merkmal sind nicht eindeutig vereinbar.",
        )
    positive_assertion, negative_assertion = any(positive), all(negative)
    if positive_assertion if value else negative_assertion:
        return "supported", "Konkrete feldbezogene Aussage stützt den booleschen Wert."
    if negative_assertion if value else positive_assertion:
        return "contradicted", "Konkrete feldbezogene Aussage widerspricht dem booleschen Wert."
    return "unclear", "Fehlender Nachweis bleibt unbekannt; Negation nur eines Führungsmerkmals genügt nicht."


def boolean_value_supported(field, value, excerpt):
    return boolean_verdict(field, value, excerpt)[0] == "supported"


def identity_question(participant_id, facts):
    return Clarification(
        question_id=f"K03:{participant_id}",
        code="K03",
        field="people",
        participant_id=participant_id,
        question="Welche der belegten beruflichen Zuordnungen gehört zur gesuchten Person?",
        reason="Mehrere aktuelle Rollen oder berufliche Identitäten sind nicht eindeutig vereinbar.",
        affected_sections=["people"],
        handling="skippable_profile",
        options=[
            f"Fakt {f.fact_id} bestätigen: {f.value} ({f.information_as_of or 'Zeitbezug offen'})"
            for f in facts
        ]
        + ["Angabe korrigieren", "Als ungeklärt überspringen"],
    )


def mark_role_conflicts(bundle):
    """Never silently choose the first current role without explicit compatibility.

    A single supporting passage must explicitly connect the multiple roles with
    an additive marker. Multiple independent role assertions alone do not prove
    a dual function. Historical roles are intentionally excluded.
    """
    source_map = {s.source_id: s for s in bundle.sources}
    subjects = {f.subject_ref for f in bundle.facts if f.field == "current_role"}
    for subject in subjects:
        roles = [
            f
            for f in bundle.facts
            if f.subject_ref == subject
            and f.field == "current_role"
            and f.status in ("verified", "conflicting")
        ]
        if len({str(f.value).strip().casefold() for f in roles}) < 2:
            continue
        # Semantic contradictions and explicit identity conflicts cannot be
        # cleared merely because a passage also contains an additive marker.
        already_conflicting = any(f.status == "conflicting" for f in roles)
        compatible = False
        if not already_conflicting:
            shared = set.intersection(*(set(f.evidence_ids) for f in roles))
            for sid in shared:
                excerpt = source_map[sid].excerpt if sid in source_map else ""
                # Limit compatibility to a single sentence, avoiding arbitrary
                # 'and' elsewhere in a source containing independent profiles.
                for sentence in re.split(r"[.!?\n]", excerpt):
                    if all(str(f.value).casefold() in sentence.casefold() for f in roles) and re.search(
                        r"\b(?:und|sowie|zugleich|gleichzeitig|zusätzlich|doppelfunktion|and|also|both)\b",
                        sentence,
                        re.I,
                    ):
                        compatible = True
        if compatible:
            continue
        for fact in roles:
            fact.status = "conflicting"
            fact.conflict = "Unterschiedliche aktuelle Rollen; Identität oder Doppelfunktion ungeklärt."
        bundle.clarifications = [q for q in bundle.clarifications if q.question_id != f"K03:{subject}"]
        bundle.clarifications.append(identity_question(subject, roles))
    return bundle


def fixture_input_assessment(input):
    """Deterministic, explicitly bounded assessment for synthetic tests only."""
    issues = []
    vague = bool(re.search(r"\b(?:irgendwas|irgendwie|mal sehen|whatever|anything)\b", input.goal, re.I))
    missing_customer_goal = input.relationship == "existing_customer" and not input.goal.strip()
    if vague or missing_customer_goal:
        issues.append(
            Clarification(
                question_id="K04:goal",
                code="K04",
                field="goal",
                question="Was soll nach dem Gespräch konkret geklärt sein?",
                reason="Das Ziel lässt unterschiedliche Vorbereitungen zu."
                if vague
                else "Das Gesprächsziel fehlt.",
                options=[],
                handling="clarify",
                affected_sections=["recommendation", "storyline"],
            )
        )
    return InputAssessment(issues=issues, evaluated=True)
