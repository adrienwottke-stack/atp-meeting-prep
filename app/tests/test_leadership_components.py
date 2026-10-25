import pytest
from test_rules import facts, meeting

from atp.models import Fact, utcnow
from atp.profiles import load_profile
from atp.rules import evaluate_icp


@pytest.mark.parametrize(
    "owner,family,expected",
    [
        (True, None, "matching"),
        (None, True, "matching"),
        (True, False, "matching"),
        (False, True, "matching"),
        (False, False, "deviating"),
        (False, None, "unclear"),
        (None, False, "unclear"),
        (None, None, "unclear"),
    ],
)
def test_split_operational_leadership_has_three_valued_or(owner, family, expected):
    evidence = facts(owner_or_family_led=None)
    for field, value in (("owner_led", owner), ("family_led", family)):
        if value is not None:
            evidence.append(
                Fact(
                    field=field,
                    value=value,
                    status="verified",
                    provenance="web_verified",
                    evidence_ids=["s1"],
                )
            )
    criterion = next(
        c
        for c in evaluate_icp(evidence, meeting(), load_profile()).criteria
        if c.field == "owner_or_family_led"
    )
    assert criterion.status == expected


def test_ownership_alone_never_establishes_leadership():
    evidence = facts(owner_or_family_led=None)
    evidence.append(
        Fact(
            field="family_ownership",
            value=True,
            status="verified",
            provenance="web_verified",
            evidence_ids=["s1"],
        )
    )
    criterion = next(
        c
        for c in evaluate_icp(evidence, meeting(), load_profile()).criteria
        if c.field == "owner_or_family_led"
    )
    assert criterion.status == "unclear"


def test_combined_and_component_disagreement_stays_unclear():
    evidence = facts(owner_or_family_led=False)
    evidence.append(
        Fact(field="owner_led", value=True, status="verified", provenance="web_verified", evidence_ids=["s2"])
    )
    criterion = next(
        c
        for c in evaluate_icp(evidence, meeting(), load_profile()).criteria
        if c.field == "owner_or_family_led"
    )
    assert criterion.status == "unclear"


def test_mixed_confirmation_is_not_promoted_to_pure_web_provenance():
    evidence = facts(owner_or_family_led=None)
    evidence.extend(
        [
            Fact(
                field="owner_led",
                value=False,
                status="verified",
                provenance="web_verified",
                evidence_ids=["s1"],
            ),
            Fact(
                field="family_led",
                value=False,
                status="verified",
                provenance="user_confirmed",
                confirmed_by="test",
                confirmed_at=utcnow(),
            ),
        ]
    )
    criterion = next(
        c
        for c in evaluate_icp(evidence, meeting(), load_profile()).criteria
        if c.field == "owner_or_family_led"
    )
    assert criterion.status == "deviating"
    assert criterion.provenance == "user_confirmed"


def test_conflicting_branch_needs_no_resolution_if_other_branch_proves_or_true():
    evidence = facts(owner_or_family_led=None)
    evidence.extend(
        [
            Fact(
                field="owner_led",
                value=True,
                status="verified",
                provenance="web_verified",
                evidence_ids=["s1"],
            ),
            Fact(field="family_led", status="conflicting"),
        ]
    )
    criterion = next(
        c
        for c in evaluate_icp(evidence, meeting(), load_profile()).criteria
        if c.field == "owner_or_family_led"
    )
    assert criterion.status == "matching"
