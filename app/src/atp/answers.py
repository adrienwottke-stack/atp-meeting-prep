"""Field-scoped resolutions, independent of provider transport and persistence."""

from __future__ import annotations

import json
import math

from .clarification_choices import MEETING_TYPES, RELATIONSHIP_CONTEXTS, RELATIONSHIPS
from .models import Clarification, ClarificationAnswer, Fact, MeetingInput, utcnow
from .rules import ICP_FIELDS

UNKNOWN_LABEL = "Als ungeklärt fortfahren"


def normalize_answer(raw, question: Clarification) -> ClarificationAnswer:
    if isinstance(raw, str):
        value = raw.strip()
        unknown = any(
            term in value.casefold()
            for term in ("ungeklärt", "überspring", "unbekannt belassen", "weiß ich nicht")
        )
        answer = ClarificationAnswer(
            action="leave_unknown" if unknown else "select" if value in question.options else "provide",
            value=value,
        )
    else:
        answer = raw if isinstance(raw, ClarificationAnswer) else ClarificationAnswer.model_validate(raw)
        answer = answer.model_copy(update={"value": answer.value.strip()})
    if answer.action == "leave_unknown":
        if question.code == "K01" or not question.allow_unknown:
            raise ValueError("INVALID_ANSWER")
        return answer.model_copy(update={"value": UNKNOWN_LABEL})
    if not answer.value:
        raise ValueError("INVALID_ANSWER")
    if (
        question.code == "K03"
        or (question.code == "K04" and question.field == "meeting_type")
        or question.field
        in (
            "relationship",
            "relationship_context",
            "company.entity_hint",
        )
    ):
        if answer.value not in question.options:
            raise ValueError("INVALID_ANSWER")
    if answer.action == "select" and answer.value not in question.options:
        raise ValueError("INVALID_ANSWER")
    return answer


def resolve_answers(data, questions, submitted, existing, actor_id):
    """Validate the entire batch before returning any field mutations."""
    data = MeetingInput.model_validate(data).model_dump(mode="json")
    questions = [Clarification.model_validate(q) for q in questions]
    by_id = {q.question_id: q for q in questions}
    if not submitted or not set(submitted) <= set(by_id):
        raise ValueError("INVALID_ANSWER")
    records = dict(existing or {})
    for qid, raw in submitted.items():
        q = by_id[qid]
        if q.answer is not None:
            raise ValueError("INVALID_ANSWER")
        answer = normalize_answer(raw, q)
        record = {
            **answer.model_dump(),
            "confirmed_by": actor_id,
            "confirmed_at": utcnow().isoformat(),
            "question": q.question or q.reason,
            "field": q.field,
            "reason": q.reason,
            "code": q.code,
            "participant_id": q.participant_id,
            "options": q.options,
        }
        if answer.action != "leave_unknown":
            value, field = answer.value, q.field
            if q.code == "K03":
                if "korrigieren" in value.casefold():
                    raise ValueError("INPUT_CORRECTION_REQUIRED")
            elif field == "relationship":
                if value not in RELATIONSHIPS:
                    raise ValueError("INVALID_ANSWER")
                data[field] = RELATIONSHIPS[value]
                if value == "Neukontakt; Anlass korrigieren":
                    raise ValueError("INPUT_CORRECTION_REQUIRED")
            elif field == "relationship_context":
                if value not in RELATIONSHIP_CONTEXTS:
                    raise ValueError("INVALID_ANSWER")
                data[field] = RELATIONSHIP_CONTEXTS[value]
            elif field == "company.entity_hint":
                if value != "Firma und Website gehören zusammen":
                    raise ValueError("INPUT_CORRECTION_REQUIRED")
                data["company"]["entity_hint"] = "Vom Nutzer bestätigt: " + value
            elif field in ("company.name", "company.website"):
                if q.code == "K01":
                    data["company"][field.split(".")[1]] = value
                else:
                    raise ValueError("INPUT_CORRECTION_REQUIRED")
            elif field in ("goal", "exception_reason"):
                data[field] = value
            elif field == "meeting_type":
                data[field] = MEETING_TYPES.get(value, value)
            elif field == "people" and q.code == "K01":
                from .models import uid

                data["people"] = [{"participant_id": uid(), "name": value}]
            elif field.startswith("people.") and field.endswith(".name") and q.code == "K01":
                participant_id = field.split(".")[1]
                for person in data["people"]:
                    if person["participant_id"] == participant_id:
                        person["name"] = value
            elif q.code == "K01":
                raise ValueError("INPUT_CORRECTION_REQUIRED")
            elif q.code == "K06" and field in ICP_FIELDS and answer.action == "provide":
                # Validate typed claims now, before a transaction accepts the answer.
                if not value.startswith(("https://", "http://")):
                    _claim_value(field, value)
        q.answer = answer.value
        records[qid] = record
    from .rules import clarify_input

    validated = MeetingInput.model_validate(data)
    invalid_core = {q.question_id for q in clarify_input(validated) if q.code == "K01"}
    if invalid_core.intersection(submitted):
        raise ValueError("INVALID_ANSWER")
    return (
        validated.model_dump(mode="json"),
        [q.model_dump(mode="json") for q in questions],
        records,
    )


def _claim_value(field, value):
    if field in ("owner_or_family_led", "is_corporation", "is_public_authority"):
        if value.casefold() in ("ja", "yes", "true"):
            return True
        if value.casefold() in ("nein", "no", "false"):
            return False
        raise ValueError("INVALID_ANSWER")
    if field == "employees_total":
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ValueError("INVALID_ANSWER") from None
        from .rules import _number_range

        if _number_range(parsed) is None or isinstance(parsed, float) and not math.isfinite(parsed):
            raise ValueError("INVALID_ANSWER")
        return parsed
    return value


def apply_confirmed_claims(bundle, records):
    """Only explicit typed user statements become facts; URL hints never do."""
    bundle = bundle.model_copy(deep=True)
    for qid, record in records.items():
        if not isinstance(record, dict) or record.get("action") != "provide" or record.get("code") != "K06":
            continue
        field, value = record.get("field"), record.get("value", "")
        if field not in ICP_FIELDS or value.startswith(("https://", "http://")):
            continue
        if not record.get("confirmed_by") or not record.get("confirmed_at"):
            continue
        fid = "user:" + qid
        bundle.facts = [f for f in bundle.facts if f.fact_id != fid]
        bundle.facts.append(
            Fact(
                fact_id=fid,
                field=field,
                value=_claim_value(field, value),
                status="verified",
                provenance="user_confirmed",
                confirmed_by=record["confirmed_by"],
                confirmed_at=record["confirmed_at"],
            )
        )
    return bundle
