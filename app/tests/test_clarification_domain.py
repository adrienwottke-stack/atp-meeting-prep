"""Versioned decision examples and state-machine regression checks."""

import json
from pathlib import Path

import pytest

from atp.clarification import CLARIFICATION_VERSION, plan_clarifications
from atp.models import Clarification, Fact, InputAssessment, MeetingInput, ResearchBundle, utcnow
from atp.profiles import load_profile
from atp.quality import assess_quality
from atp.rules import evaluate_icp, recommend

CATALOG = json.loads((Path(__file__).parent / "fixtures/clarification-scenarios-v1.json").read_text("utf-8"))


def setup_case(case):
    payload = {
        "company": {"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
        "people": [{"participant_id": "lea", "name": "Lea Sommer"}],
        "meeting_type": "setup",
        "relationship": "new_contact",
        "goal": "Nächsten Schritt klären",
        "confirmed": True,
        "public_identifiers_confirmed": True,
    }
    payload.update(case.get("input", {}))
    data = MeetingInput.model_validate(payload)
    values = {
        "employees_total": 120,
        "knowledge_workers": 40,
        "owner_or_family_led": True,
        "segment": "Mittelstand",
        "is_corporation": False,
        "is_public_authority": False,
    }
    values.update(case.get("fact_updates", {}))
    facts = [
        Fact(field=field, value=value, status="verified", provenance="web_verified", evidence_ids=["s1"])
        for field, value in values.items()
        if field not in case.get("omit", [])
    ]
    if case.get("empty"):
        facts = []
    questions = [Clarification.model_validate(q) for q in case.get("bundle_questions", [])]
    bundle = ResearchBundle(
        facts=facts, gaps=case.get("gaps", []), limits=case.get("limits", []), clarifications=questions
    )
    issues = [Clarification.model_validate(q) for q in case.get("assessment", [])]
    assessment = InputAssessment(issues=issues, evaluated=True)
    icp = evaluate_icp(facts, data, load_profile())
    return data, bundle, icp, assessment


@pytest.mark.parametrize("case", CATALOG["scenarios"], ids=lambda case: case["id"])
def test_versioned_scenarios(case):
    data, bundle, icp, assessment = setup_case(case)
    answers = case.get("answers", {})
    plan = plan_clarifications(
        data,
        bundle,
        icp,
        assessment=assessment,
        answers=answers,
        rounds=case.get("rounds", 0),
    )
    assert [q.question_id for q in plan.open_questions] == case["open"]
    assert [q.question_id for q in plan.questions] == case.get("asked", case["open"][:3])
    assert plan.exhausted == case.get("exhausted", False)
    recommendation = recommend(
        data,
        icp,
        company_clear=not any(q.code in ("K01", "K02") for q in plan.open_questions),
        goal_clear=not any(q.code == "K04" for q in plan.open_questions),
    )
    quality = assess_quality(
        data, bundle, icp, recommendation, open_questions=plan.open_questions, answers=answers
    )
    assert quality.state == case["quality"]
    assert len(plan.questions) <= 3
    assert all(q.question and q.reason and q.affected_sections for q in plan.open_questions)
    assert len({q.question_id for q in plan.open_questions}) == len(plan.open_questions)


def test_catalog_has_version_and_at_least_twenty_independent_cases():
    assert CATALOG["version"] == CLARIFICATION_VERSION
    assert len(CATALOG["scenarios"]) >= 20
    assert len({c["id"] for c in CATALOG["scenarios"]}) == len(CATALOG["scenarios"])


def test_research_gaps_never_consume_user_rounds_but_remain_open():
    data, bundle, icp, _ = setup_case({"empty": True})
    first = plan_clarifications(data, bundle, icp)
    assert first.questions == []
    second = plan_clarifications(data, bundle, icp, rounds=1)
    assert second.questions == []
    assert len(second.open_questions) == 5
    exhausted = plan_clarifications(data, bundle, icp, rounds=2)
    assert exhausted.questions == []
    assert not exhausted.exhausted
    assert len(exhausted.open_questions) == 5


def test_real_question_is_asked_even_with_many_research_gaps():
    data, bundle, icp, _ = setup_case({"empty": True})
    assessment = InputAssessment(
        issues=[
            Clarification(
                code="K04",
                field="goal",
                reason="Auftragsziel mehrdeutig",
                options=[],
                handling="clarify",
            )
        ]
    )
    plan = plan_clarifications(data, bundle, icp, assessment=assessment)
    assert [q.field for q in plan.questions] == ["goal"]
    assert len([q for q in plan.open_questions if q.code == "K06"]) == 5


def test_goal_instruction_is_not_offered_as_a_selectable_answer():
    data, bundle, icp, _ = setup_case({"input": {"relationship": "existing_customer", "goal": ""}})
    goal = next(q for q in plan_clarifications(data, bundle, icp).questions if q.field == "goal")
    assert goal.options == [] and goal.answer_types == ["provide", "leave_unknown"]


def test_identity_waits_without_withholding_independent_company_sections():
    data, bundle, icp, _ = setup_case({})
    bundle.clarifications = [
        Clarification(
            code="K03",
            field="people",
            participant_id="lea",
            reason="Namensvetter",
            options=["Kandidatin A", "Kandidatin B"],
            handling="skippable_profile",
        )
    ]
    plan = plan_clarifications(data, bundle, icp)
    assert plan.withheld_sections == ["people:lea"]
    assert plan.questions[0].allow_unknown


def test_hard_input_never_offers_unknown_even_after_round_limit():
    data, bundle, icp, _ = setup_case({"input": {"people": []}})
    plan = plan_clarifications(
        data, bundle, icp, rounds=2, answers={"K01:people": {"action": "leave_unknown"}}
    )
    assert plan.questions[0].question_id == "K01:people"
    assert plan.questions[0].answer_types == ["provide"]
    assert not plan.questions[0].allow_unknown


def test_random_model_question_ids_are_canonicalized_and_do_not_repeat():
    data, bundle, icp, _ = setup_case({})
    assessment = InputAssessment(
        issues=[
            Clarification(
                question_id="random-model-id",
                code="K04",
                field="goal",
                reason="Zwei mögliche Aufträge",
                options=["Ziel A", "Ziel B"],
                handling="blocking_dependent_output",
            )
        ]
    )
    plan = plan_clarifications(data, bundle, icp, assessment=assessment, answers={"K04:goal": "Ziel A"})
    assert plan.questions == []
    assert plan.open_questions == []


def test_user_facts_remain_user_confirmed_in_quality_assessment():
    data, bundle, icp, _ = setup_case({})
    for fact in bundle.facts:
        fact.provenance, fact.evidence_ids = "user_confirmed", []
        fact.confirmed_by, fact.confirmed_at = "local-user", utcnow()
    quality = assess_quality(data, bundle, icp, recommend(data, icp), open_questions=[])
    assert quality.state == "sufficient"
    assert all(f.provenance == "user_confirmed" and not f.evidence_ids for f in bundle.facts)


def test_no_company_facts_never_sufficient_even_with_explicit_empty_open_list():
    data, bundle, _, _ = setup_case(
        {"empty": True, "input": {"relationship": "ongoing_project", "meeting_type": "project_review"}}
    )
    icp = evaluate_icp(bundle.facts, data, load_profile())
    quality = assess_quality(data, bundle, icp, recommend(data, icp), open_questions=[])
    assert quality.state == "limited"
    assert any("Firmenfakten" in reason for reason in quality.reasons)


def test_legacy_research_entrypoint_uses_central_missing_goal_rule():
    from atp.rules import research_clarifications

    data, bundle, icp, _ = setup_case(
        {
            "input": {
                "relationship": "existing_customer",
                "relationship_context": "existing_obligation",
                "goal": "",
            }
        }
    )
    assert [q.question_id for q in research_clarifications(data, bundle, icp)] == ["K04:goal"]


@pytest.mark.parametrize("field", ["relationship", "relationship_context", "meeting_type"])
def test_model_options_for_fixed_fields_are_answerable_and_preserve_explicit_unknown(field):
    from atp.answers import resolve_answers

    data, bundle, icp, _ = setup_case({})
    assessment = InputAssessment(
        issues=[
            Clarification(
                code="K04",
                field=field,
                question="An unsupported model question",
                reason="Model uncertainty",
                options=["Unsupported model option"],
                handling="clarify",
            )
        ]
    )
    plan = plan_clarifications(data, bundle, icp, assessment=assessment)
    q = plan.questions[0]
    assert q.answer_types == ["select", "leave_unknown"] and q.allow_unknown
    for option in q.options:
        changed, _, _ = resolve_answers(
            data.model_dump(mode="json"),
            [q.model_dump(mode="json")],
            {q.question_id: {"action": "select", "value": option}},
            {},
            "actor",
        )
        assert changed[field] != "unknown"
    unchanged, _, _ = resolve_answers(
        data.model_dump(mode="json"),
        [q.model_dump(mode="json")],
        {q.question_id: {"action": "leave_unknown"}},
        {},
        "actor",
    )
    assert unchanged == data.model_dump(mode="json")


def test_fixed_choice_repair_preserves_rule_specific_correction_options():
    from atp.answers import resolve_answers

    data, bundle, icp, _ = setup_case({"input": {"meeting_type": "project_review"}})
    q = plan_clarifications(data, bundle, icp).questions[0]
    assert q.options == ["Laufendes Projekt", "Bestandskunde", "Neukontakt; Anlass korrigieren"]
    with pytest.raises(ValueError, match="INPUT_CORRECTION_REQUIRED"):
        resolve_answers(
            data.model_dump(mode="json"),
            [q.model_dump(mode="json")],
            {q.question_id: "Neukontakt; Anlass korrigieren"},
            {},
            "actor",
        )
