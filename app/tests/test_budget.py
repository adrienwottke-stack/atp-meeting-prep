import pytest

from atp.budget import Budget, BudgetExceeded, LeaseLost
from atp.config import Settings


def test_failed_attempts_and_retries_remain_reserved():
    snapshots = []
    budget = Budget(
        Settings(
            _env_file=None,
            deadline_seconds=300,
            research_cutoff_seconds=240,
            max_model_requests=8,
            max_search_calls=12,
            max_evidence_fetches=20,
        ),
        persist=lambda u: snapshots.append(u.copy()),
    )
    for _ in range(8):
        budget.reserve(model=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve(model=1)
    assert snapshots[-1]["model_requests"] == 8


def test_resume_carries_budget_and_active_seconds():
    budget = Budget(
        Settings(
            _env_file=None,
            deadline_seconds=300,
            research_cutoff_seconds=240,
            max_model_requests=8,
            max_search_calls=12,
            max_evidence_fetches=20,
        ),
        {"active_seconds": 299.99, "model_requests": 8},
    )
    with pytest.raises(BudgetExceeded):
        budget.reserve(model=1)
    assert budget.remaining_seconds <= 0.01


def test_budget_reservations_are_all_or_nothing():
    budget = Budget(
        Settings(
            _env_file=None,
            deadline_seconds=300,
            research_cutoff_seconds=240,
            max_model_requests=8,
            max_search_calls=12,
            max_evidence_fetches=20,
        ),
        {"search_calls": 12},
    )
    with pytest.raises(BudgetExceeded):
        budget.reserve(model=1, searches=1)
    assert budget.usage.model_requests == 0


def test_lease_fencing_prevents_provider_call():
    budget = Budget(
        Settings(
            _env_file=None,
            deadline_seconds=300,
            research_cutoff_seconds=240,
            max_model_requests=8,
            max_search_calls=12,
            max_evidence_fetches=20,
        ),
        persist=lambda u: False,
    )
    with pytest.raises(LeaseLost):
        budget.reserve(model=1)


def test_research_cutoff_leaves_synthesis_budget():
    budget = Budget(
        Settings(
            _env_file=None,
            deadline_seconds=300,
            research_cutoff_seconds=240,
            max_model_requests=8,
            max_search_calls=12,
            max_evidence_fetches=20,
        ),
        {"active_seconds": 241},
    )
    with pytest.raises(BudgetExceeded):
        budget.reserve(model=1, searches=1)
    budget.reserve(model=1)
    assert budget.remaining_seconds < 60


def test_approved_extended_defaults_and_hard_caps():
    settings = Settings(_env_file=None)
    assert (settings.deadline_seconds, settings.research_cutoff_seconds) == (480, 390)
    assert (settings.max_model_requests, settings.max_search_calls, settings.max_evidence_fetches) == (
        16,
        24,
        40,
    )
    budget = Budget(settings, {"model_requests": 16})
    with pytest.raises(BudgetExceeded):
        budget.reserve(model=1)
    budget = Budget(settings, {"active_seconds": 391})
    with pytest.raises(BudgetExceeded):
        budget.reserve(searches=1)
    budget.reserve(model=1)
    assert budget.remaining_seconds < 90
