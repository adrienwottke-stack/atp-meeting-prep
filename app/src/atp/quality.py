"""Assess usability independently of technical completion."""

from __future__ import annotations

from .clarification import plan_clarifications
from .models import (
    Clarification,
    ICPResult,
    MeetingInput,
    QualityAssessment,
    Recommendation,
    RecommendationValue,
    ResearchBundle,
)
from .rules import LABELS, _eligible


def assess_quality(
    input: MeetingInput,
    bundle: ResearchBundle,
    icp: ICPResult,
    recommendation: Recommendation,
    *,
    open_questions: list[Clarification] | None = None,
    answers: dict | None = None,
) -> QualityAssessment:
    if open_questions is None:
        open_questions = plan_clarifications(input, bundle, icp, answers=answers).open_questions
    blockers = [q for q in open_questions if q.code in ("K01", "K02", "K03", "K04")]
    reasons = []
    for q in blockers:
        reasons.append(
            f"{q.question or q.reason} {q.reason}" if q.question and q.question != q.reason else q.reason
        )
    research_gaps = [q for q in open_questions if q.code == "K06"]
    reasons.extend(
        f"Nicht ausreichend belegt: {LABELS.get(q.field, q.field)}. {q.reason}" for q in research_gaps
    )
    has_company_facts = any(_eligible(f) for f in bundle.facts)
    if not has_company_facts:
        reasons.append("Es liegen keine verifizierten oder ausdrücklich nutzerbestätigten Firmenfakten vor.")
    if recommendation.value == RecommendationValue.CLARIFY and not blockers:
        reasons.append("Die Terminempfehlung ist nur eingeschränkt belastbar: " + recommendation.reason)
    if blockers:
        return QualityAssessment(state="clarification_required", reasons=list(dict.fromkeys(reasons)))
    # Unknown supplementary ICP details are still disclosed even if an existing
    # obligation makes them immaterial to attendance.
    reasons.extend(bundle.gaps)
    reasons.extend(bundle.limits)
    reasons.extend(icp.open_points)
    if any(f.status in ("unknown", "conflicting") for f in bundle.facts):
        reasons.append(
            "Einzelne recherchierte Angaben sind ungeprüft oder widersprüchlich und bleiben offen."
        )
    return QualityAssessment(
        state="limited" if reasons else "sufficient",
        reasons=list(dict.fromkeys(reasons)),
    )
