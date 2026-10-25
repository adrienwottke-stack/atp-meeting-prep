"""Bounded, deterministic decisions about user clarification.

The assessment/provider may identify uncertainty; only this module decides what
to ask and which outputs depend on it. Unknown answers never establish facts.
"""

from __future__ import annotations

from .models import (
    Clarification,
    ClarificationPlan,
    ICPResult,
    InputAssessment,
    MeetingInput,
    Relationship,
    ResearchBundle,
)
from .rules import LABELS, _q, clarify_input

CLARIFICATION_VERSION = "atp-clarification-2"
MAX_ROUNDS = 2
MAX_QUESTIONS = 3

_QUESTIONS = {
    "company.name": "Wie heißt die angefragte Firma?",
    "company.website": "Wie lautet die öffentliche Website der angefragten Firma?",
    "company.entity_hint": "Gehören Firma und Website zur angefragten Unternehmenseinheit?",
    "people": "Wer nimmt an dem Gespräch teil?",
    "meeting_type": "Was ist der bestätigte Anlass des Gesprächs?",
    "goal": "Was soll nach dem Gespräch konkret geklärt oder erreicht sein?",
    "relationship": "Ist dies ein Neukontakt, ein Bestandskunde oder ein laufendes Projekt?",
    "relationship_context": "Geht es um eine bestehende Verpflichtung oder eine neue Vertriebsanfrage?",
}


def answer_left_unknown(answer) -> bool:
    """Recognize structured resolutions and the previously supported UI strings."""
    if hasattr(answer, "model_dump"):
        answer = answer.model_dump()
    if isinstance(answer, dict):
        return answer.get("action") == "leave_unknown"
    if isinstance(answer, str):
        value = answer.casefold().strip()
        return any(
            term in value for term in ("ungeklärt", "unbekannt belassen", "überspring", "weiß ich nicht")
        )
    return False


def _enrich(question: Clarification, input: MeetingInput) -> Clarification:
    q = question.model_copy(deep=True)
    if q.code == "K01":
        sections = ["company", "people", "recommendation", "opportunities", "storyline"]
        q.allow_unknown, q.answer_types = False, ["provide"]
    elif q.code == "K02":
        sections = ["company", "recommendation", "opportunities", "storyline"]
    elif q.code == "K03":
        sections = [f"people:{q.participant_id}"] if q.participant_id else ["people"]
        person = next((p for p in input.people if p.participant_id == q.participant_id), None)
        q.question = q.question or (
            f"Welche berufliche Identität beziehungsweise aktuelle Rolle gehört zu {person.name}?"
            if person
            else "Welche berufliche Identität beziehungsweise aktuelle Rolle ist gemeint?"
        )
    elif q.code == "K05":
        sections = ["transfer"]
    else:
        sections = ["recommendation"]
        if q.code == "K04":
            sections += ["opportunities", "storyline"]
        if q.code == "K06":
            q.answer_types = ["provide", "leave_unknown"]
    q.affected_sections = q.affected_sections or sections
    if not q.allow_unknown:
        q.answer_types = [a for a in q.answer_types if a != "leave_unknown"]
    if not q.question:
        q.question = _QUESTIONS.get(q.field, "")
        if not q.question and q.field in LABELS:
            q.question = f"Welche belastbare Angabe liegt zu „{LABELS[q.field]}“ für diese Einheit vor?"
        if not q.question and q.field.startswith("people.") and q.field.endswith(".name"):
            q.question = "Wie heißt diese teilnehmende Person?"
        q.question = q.question or q.reason
    return q


def _icp_matters(input: MeetingInput, icp: ICPResult) -> bool:
    if input.relationship == Relationship.PROJECT:
        return False
    if input.relationship == Relationship.EXISTING and input.relationship_context != "new_sales_inquiry":
        return False
    # Ask relationship before five possibly unnecessary qualification questions.
    if input.relationship == Relationship.UNKNOWN:
        return False
    # An already proven exclusion fixes the new-sales recommendation regardless
    # of unknown secondary ICP fields, with or without a documented exception.
    return "exclusion" not in icp.deviation_kinds


def _candidates(input: MeetingInput, bundle: ResearchBundle, icp: ICPResult, assessment):
    questions = [*clarify_input(input), *bundle.clarifications]
    if assessment is not None:
        questions.extend(assessment.issues)
    if input.relationship == Relationship.UNKNOWN and icp.status != "matching":
        questions.append(
            _q(
                "K04",
                "relationship",
                "Die Kundenbeziehung kann die Terminempfehlung verändern.",
                ["Neukontakt", "Bestandskunde", "Laufendes Projekt"],
                "blocking_dependent_output",
            )
        )
    if (
        input.relationship == Relationship.EXISTING
        and input.relationship_context == "unspecified"
        and icp.status != "matching"
    ):
        questions.append(
            _q(
                "K04",
                "relationship_context",
                "Der ICP-Befund hat unterschiedliche Folgen für bestehende Verpflichtungen und neue Vertriebsanfragen.",
                ["Bestehende Verpflichtung", "Neue Vertriebsanfrage"],
                "blocking_dependent_output",
            )
        )
    if _icp_matters(input, icp):
        for criterion in icp.criteria:
            if criterion.status == "unclear":
                questions.append(
                    _q(
                        "K06",
                        criterion.field,
                        criterion.reason,
                        ["Angabe oder öffentlichen Beleg ergänzen", "Als ungeklärt fortfahren"],
                        "blocking_dependent_output",
                    )
                )
    return questions


def plan_clarifications(
    input: MeetingInput,
    bundle: ResearchBundle,
    icp: ICPResult,
    *,
    assessment: InputAssessment | None = None,
    answers: dict | None = None,
    rounds: int = 0,
) -> ClarificationPlan:
    """Return one bounded question batch and the complete remaining uncertainty.

    Answers are accepted/validated by the workflow before reaching this function.
    A supplied answer suppresses repeat questions, while deliberately unknown
    resolutions remain visible and continue withholding dependent outputs.
    """
    answers = answers or {}
    priority = {"K01": 0, "K02": 1, "K03": 2, "K04": 3, "K06": 4, "K05": 5}
    unique = {}
    for raw in _candidates(input, bundle, icp, assessment):
        # Supplemental gaps and transfer approvals do not interrupt preparation.
        if raw.code == "K05":
            continue
        if raw.code == "K06":
            relevant = any(c.field == raw.field and c.status == "unclear" for c in icp.criteria)
            if not relevant or not _icp_matters(input, icp):
                continue
        q = _enrich(raw, input)
        q.question_id = f"{q.code}:{q.participant_id or q.field}"
        # Fixed rules win over model wording for the same stable question ID.
        unique.setdefault(q.question_id, q)
    ordered = sorted(unique.values(), key=lambda q: priority[q.code])
    unresolved = [
        q
        for q in ordered
        if q.question_id not in answers or answer_left_unknown(answers[q.question_id]) or q.code == "K06"
    ]
    # Research gaps stay visible in the briefing and constrain the recommendation.
    # They are the agent's responsibility, never homework for the user.
    unanswered = [q for q in unresolved if q.question_id not in answers and q.code != "K06"]
    # Preflight K01 cannot be bypassed by claiming an unknown answer or a round
    # counter. The coordinator handles this phase before research starts.
    hard = [q for q in unresolved if q.code == "K01"]
    exhausted = rounds >= MAX_ROUNDS and any(q.code not in ("K01", "K06") for q in unresolved)
    batch = hard[:MAX_QUESTIONS] if hard else ([] if rounds >= MAX_ROUNDS else unanswered[:MAX_QUESTIONS])
    sections = list(dict.fromkeys(section for q in unresolved for section in q.affected_sections))
    return ClarificationPlan(
        questions=batch,
        open_questions=unresolved,
        withheld_sections=sections,
        exhausted=exhausted,
    )
