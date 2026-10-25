"""API resolutions preserve field scope, provenance and explicit uncertainty."""

import pytest
from pydantic import ValidationError

from atp.answers import apply_confirmed_claims, resolve_answers
from atp.models import Clarification, MeetingInput, ResearchBundle
from atp.web import Answers


def data():
    return MeetingInput(
        company={"name": "Unit GmbH", "website": "https://unit.example"},
        people=[{"participant_id": "p1", "name": "Person A"}, {"participant_id": "p2", "name": "Person B"}],
        meeting_type="setup",
        relationship="existing_customer",
        relationship_context="existing_obligation",
        confirmed=True,
        public_identifiers_confirmed=True,
    ).model_dump(mode="json")


def question(code="K04", field="goal", **kwargs):
    return Clarification(
        question_id=f"{code}:{field}",
        code=code,
        field=field,
        reason="Needed for dependent output",
        options=[],
        handling="blocking_dependent_output",
        **kwargs,
    ).model_dump(mode="json")


def test_answer_provenance_and_only_target_field_changes():
    original = data()
    changed, questions, records = resolve_answers(
        original,
        [question()],
        {"K04:goal": {"action": "provide", "value": "Nächsten Schritt abstimmen"}},
        {},
        "actor",
    )
    assert changed["goal"] == "Nächsten Schritt abstimmen"
    assert changed["people"] == original["people"] and original["goal"] == ""
    assert records["K04:goal"]["confirmed_by"] == "actor"
    assert records["K04:goal"]["confirmed_at"]
    assert questions[0]["answer"]


def test_unknown_is_explicit_and_does_not_change_input():
    changed, _, records = resolve_answers(
        data(), [question()], {"K04:goal": {"action": "leave_unknown"}}, {}, "actor"
    )
    assert changed["goal"] == ""
    assert records["K04:goal"]["action"] == "leave_unknown"


def test_core_fields_cannot_be_skipped():
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        resolve_answers(
            data(),
            [question("K01", "company.name", allow_unknown=False)],
            {"K01:company.name": {"action": "leave_unknown"}},
            {},
            "actor",
        )


def test_partial_batch_preserves_pending_question_and_legacy_strings():
    _, questions, records = resolve_answers(
        data(), [question(), question(field="exception_reason")], {"K04:goal": "Ziel bestätigen"}, {}, "actor"
    )
    assert len(records) == 1 and questions[1]["answer"] is None


def test_person_selection_never_overwrites_participants():
    q = question("K03", "people", participant_id="p1")
    q["options"] = ["Kandidat f1 bestätigen"]
    changed, _, _ = resolve_answers(
        data(), [q], {"K03:people": {"action": "select", "value": q["options"][0]}}, {}, "actor"
    )
    assert changed["people"] == data()["people"]


def test_user_claims_have_provenance_but_urls_are_not_facts():
    qs = [question("K06", "employees_total"), question("K06", "owner_or_family_led")]
    _, _, records = resolve_answers(
        data(),
        qs,
        {
            "K06:employees_total": {"action": "provide", "value": "120"},
            "K06:owner_or_family_led": {"action": "provide", "value": "https://unit.example/about"},
        },
        {},
        "actor",
    )
    bundle = apply_confirmed_claims(ResearchBundle(), records)
    assert len(bundle.facts) == 1 and bundle.facts[0].value == 120
    assert bundle.facts[0].provenance == "user_confirmed" and not bundle.facts[0].evidence_ids
    assert len(apply_confirmed_claims(bundle, records).facts) == 1


@pytest.mark.parametrize("value", ["unbekannt", "-1", "true", "null", '{"min":100,"max":2}'])
def test_invalid_typed_claims_are_rejected_before_acceptance(value):
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        resolve_answers(
            data(),
            [question("K06", "employees_total")],
            {"K06:employees_total": {"action": "provide", "value": value}},
            {},
            "actor",
        )


def test_client_cannot_forge_confirmation_metadata():
    with pytest.raises(ValidationError):
        Answers.model_validate(
            {
                "expected_version": 1,
                "answers": {
                    "K04:goal": {"action": "provide", "value": "Goal", "confirmed_by": "another-user"}
                },
            }
        )


def test_unknown_question_or_already_answered_question_rejected():
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        resolve_answers(data(), [question()], {"other": "Injected"}, {}, "actor")
    q = question()
    q["answer"] = "Previously answered"
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        resolve_answers(data(), [q], {"K04:goal": "Overwrite"}, {}, "actor")


def test_waiting_model_questions_without_options_accept_canonical_choices():
    """The reported batch used text inputs for two closed input enums."""
    questions = [question(field=field) for field in ("goal", "relationship", "relationship_context")]
    for q in questions:
        q.update(options=[], answer_types=["provide", "select", "leave_unknown"])
    changed, resolved, records = resolve_answers(
        data(),
        questions,
        {
            "K04:goal": {"action": "provide", "value": "Wir wollen mehr Kunden gewinnen"},
            "K04:relationship": {"action": "select", "value": "Neukontakt"},
            "K04:relationship_context": {"action": "select", "value": "Neue Vertriebsanfrage"},
        },
        {},
        "actor",
    )
    assert changed["relationship"] == "new_contact"
    assert changed["relationship_context"] == "new_sales_inquiry"
    assert changed["goal"] == "Wir wollen mehr Kunden gewinnen"
    assert all(q["answer"] for q in resolved) and len(records) == 3
    assert all(records[qid]["confirmed_by"] == "actor" for qid in records)


def test_free_text_relationship_is_rejected_without_losing_other_answers():
    original = data()
    questions = [question(), question(field="relationship")]
    with pytest.raises(ValueError, match="INVALID_ANSWER"):
        resolve_answers(
            original,
            questions,
            {
                "K04:goal": "Wir wollen mehr Kunden gewinnen",
                "K04:relationship": "neue Kundenbeziehung",
            },
            {},
            "actor",
        )
    assert original == data() and all(q.get("answer") is None for q in questions)
