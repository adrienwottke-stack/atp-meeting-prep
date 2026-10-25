from copy import deepcopy
from datetime import date, timedelta

import pytest

from atp.models import Fact, ResearchBundle
from atp.role_currency import enforce_current_role_currency

EVALUATED = date(2026, 10, 15)


def bundle(as_of, quote, **changes):
    payload = {
        "subject_ref": "p1",
        "field": "current_role",
        "value": "Chief Executive Officer",
        "status": "verified",
        "provenance": "web_verified",
        "evidence_ids": ["s1"],
        "information_as_of": as_of,
        "supporting_quote": quote,
    }
    payload.update(changes)
    return ResearchBundle(facts=[Fact(**payload)])


@pytest.mark.parametrize(
    "as_of,quote",
    [
        ("2026-10-01", "As of 2026-10-01 Lea is Chief Executive Officer."),
        ("2026-10-01", "Stand 1. Oktober 2026: Lea ist Chief Executive Officer."),
        ("2026-10-01", "October 1, 2026: Lea is Chief Executive Officer."),
        ("2026-10-01", "1 October 2026: Lea is Chief Executive Officer."),
        ("01.10.2026", "Stand 01.10.2026: Lea ist Chief Executive Officer."),
        ("1. Oktober 2026", "2026-10-01: Chief Executive Officer Lea."),
        ("October 1, 2026", "1. Oktober 2026: Chief Executive Officer Lea."),
    ],
)
def test_explicit_matching_full_dates_keep_recent_current_role(as_of, quote):
    original = bundle(as_of, quote)
    before = deepcopy(original.model_dump())
    result = enforce_current_role_currency(original, EVALUATED)
    assert result.facts[0].field == "current_role"
    assert result.gaps == []
    assert original.model_dump() == before


@pytest.mark.parametrize(
    "as_of,quote",
    [
        ("2026", "Chief Executive Officer since January 1, 2026."),
        ("2026-10", "Chief Executive Officer as of 2026-10-01."),
        ("2026-10-01", "Chief Executive Officer in 2026."),
        ("2026-10-01", "Chief Executive Officer as of 2025-10-01."),
        ("2026-10-01", ""),
        ("2026-10-01", "Published 2026-10-01. Lea was Chief Executive Officer in 2021."),
        ("2026-10-01", "Published 2026-10-01: Lea is Chief Executive Officer."),
        ("2026-10-01", "2026-10-01: Another project was announced. Lea was Chief Executive Officer."),
        ("2026-10-16", "As of 2026-10-16 Lea is Chief Executive Officer."),
        ("2026-01-01", "Since January 1, 2026: Chief Executive Officer Lea."),
        ("2026-02-30", "2026-02-30: Chief Executive Officer Lea."),
        ("10/01/2026", "10/01/2026: Chief Executive Officer Lea."),
    ],
)
def test_stale_future_incomplete_fabricated_or_unrelated_dates_downgrade(as_of, quote):
    result = enforce_current_role_currency(bundle(as_of, quote), EVALUATED)
    item = result.facts[0]
    assert item.field == "undated_role"
    assert item.status == "verified" and item.provenance == "web_verified"
    assert item.information_as_of == as_of
    assert item.value == "Chief Executive Officer"
    assert len(result.gaps) == 1
    assert "aktuelle berufliche Rolle" in result.gaps[0]


def test_180_day_boundary_is_inclusive_and_next_day_is_stale():
    for age, expected in ((180, "current_role"), (181, "undated_role"), (0, "current_role")):
        as_of = (EVALUATED - timedelta(days=age)).isoformat()
        result = enforce_current_role_currency(
            bundle(as_of, f"As of {as_of}: Chief Executive Officer Lea."), EVALUATED
        )
        assert result.facts[0].field == expected


@pytest.mark.parametrize("title", ["Dr.", "Prof.", "Prof. Dr."])
def test_recent_dated_speaker_heading_keeps_role_across_academic_title(title):
    quote = (
        f"08.09.2026 Dankesrede von {title} Nicola Leibinger-Kammüller "
        "Vorsitzende des Vorstands TRUMPF SE + Co. KG"
    )
    result = enforce_current_role_currency(
        bundle("08.09.2026", quote, value="Vorsitzende des Vorstands"), EVALUATED
    )
    assert result.facts[0].field == "current_role"
    assert result.facts[0].supporting_quote == quote
    assert result.gaps == []


def test_academic_title_exception_does_not_join_separate_sentences_or_publication_dates():
    for quote in (
        "08.09.2026: Andere Veranstaltung. Dr. Lea ist Chief Executive Officer.",
        "Published 08.09.2026: Dr. Lea ist Chief Executive Officer.",
    ):
        result = enforce_current_role_currency(bundle("08.09.2026", quote), EVALUATED)
        assert result.facts[0].field == "undated_role"


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "historical"},
        {"provenance": "user_confirmed"},
        {"status": "unknown"},
        {"status": "conflicting"},
        {"field": "career"},
    ],
)
def test_historical_user_confirmed_and_noncurrent_candidates_are_unchanged(changes):
    original = bundle("2020", "", **changes)
    assert enforce_current_role_currency(original, EVALUATED).model_dump() == original.model_dump()


def test_real_orgchart_shape_with_year_only_does_not_establish_current_schick_role():
    original = bundle(
        "2026",
        "Schaeffler Group organizational and leadership structure since January 1, 2026 Andreas Schick Production, SCM & Purchasing",
        subject_ref="schick",
        value="Production, SCM & Purchasing",
    )
    result = enforce_current_role_currency(original, EVALUATED)
    assert result.facts[0].field == "undated_role"
    assert result.facts[0].information_as_of == "2026"
    # Even an explicit Jan-1 full date fails the 180-day currency bound in Oct.
    original.facts[0].information_as_of = "2026-01-01"
    assert enforce_current_role_currency(original, EVALUATED).facts[0].field == "undated_role"


def test_role_omitted_from_dated_quote_cannot_be_promoted_and_reconcile_is_idempotent():
    result = enforce_current_role_currency(
        bundle("2026-10-01", "2026-10-01: unrelated Chief Financial Officer."), EVALUATED
    )
    assert result.facts[0].field == "undated_role"
    assert enforce_current_role_currency(result, EVALUATED).model_dump() == result.model_dump()
