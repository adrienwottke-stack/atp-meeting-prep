from datetime import UTC, datetime

import pytest

from atp.integrations import crm_target, reduce_booking
from atp.models import BookingEvent, Company, MeetingInput


def event(kind="created", revision=1, event_id="one", meeting=None):
    return BookingEvent(
        source="controlled-fixture",
        event_id=event_id,
        booking_id="b1",
        revision=revision,
        occurred_at=datetime.now(UTC),
        kind=kind,
        meeting=meeting,
    )


def test_T15_duplicate_reschedule_cancel_late_created():
    data = MeetingInput(company=Company(name="Musterwerk GmbH", website="https://musterwerk.example"))
    state = reduce_booking(None, event(meeting=data))
    state.research_required = False
    state = reduce_booking(state, event(meeting=data))
    assert state.revision == 1 and len(state.event_ids) == 1
    moved = data.model_copy(update={"scheduled_at": datetime(2026, 11, 1, tzinfo=UTC)})
    state = reduce_booking(state, event("rescheduled", 2, "two", moved))
    assert state.input["scheduled_at"].startswith("2026-11-01")
    assert not state.research_required
    state = reduce_booking(state, event("cancelled", 3, "three"))
    state = reduce_booking(state, event("created", 1, "late-created", data))
    assert state.status == "cancelled" and not state.research_required


def test_T15_K05_only_blocks_transfer():
    target, clarification = crm_target(["contact-1", "company-2"])
    assert target is None and clarification.code == "K05"
    assert clarification.handling == "blocking_transfer"
    assert crm_target(["same", "same"]) == ("same", None)


def test_booking_contract_rejects_unrecognized_kinds_and_versions():
    with pytest.raises(ValueError):
        event(kind="send_email")
    with pytest.raises(ValueError):
        BookingEvent(
            schema_version=2,
            source="fixture",
            event_id="e",
            booking_id="b",
            revision=1,
            occurred_at=datetime.now(UTC),
            kind="cancelled",
        )


def test_reschedule_generated_participant_ids_do_not_trigger_research():
    data = MeetingInput(
        company=Company(name="Musterwerk GmbH", website="https://musterwerk.example"),
        people=[{"name": "Lea Sommer"}, {"name": "Tobias Kern"}],
    )
    state = reduce_booking(None, event(meeting=data))
    same_business_input = MeetingInput.model_validate(
        {**data.model_dump(), "people": [{"name": "Lea Sommer"}, {"name": "Tobias Kern"}]}
    )
    moved = reduce_booking(state, event("rescheduled", 2, "new-ids", same_business_input))
    assert moved.research_required is False


def test_invalid_event_does_not_consume_event_id_or_revision():
    data = MeetingInput(company=Company(name="Musterwerk GmbH", website="https://musterwerk.example"))
    state = reduce_booking(None, event(meeting=data))
    with pytest.raises(ValueError):
        reduce_booking(state, event("rescheduled", 2, "invalid"))
    assert state.revision == 1 and "invalid" not in state.event_ids


def test_crm_export_idempotency_and_unknown_outcome_require_reconciliation():
    from atp.integrations import FixtureCRMAdapter, deliver_export
    from atp.models import CRMExport, Decision, ICPResult, uid

    payload = CRMExport(
        meeting_id=uid(),
        result_version=1,
        target_system="controlled-fixture",
        target_record_id="record-one",
        approved_by="fixture-user",
        approved_at=datetime.now(UTC),
        summary=["Kontrolliertes Briefing"],
        icp=ICPResult(status="unclear", criteria=[]),
        decision=Decision(
            original_recommendation="Vorab klären",
            confirmed_decision="Vorab klären",
            decided_by="fixture-user",
        ),
        protected_url="https://internal.example/briefings/one",
    )
    adapter = FixtureCRMAdapter()
    one = deliver_export(adapter, payload)
    assert deliver_export(adapter, payload) == one and len(adapter.records) == 1
    other = payload.model_copy(update={"meeting_id": uid()})
    with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
        deliver_export(adapter, other, previous_outcome="unknown")
    assert len(adapter.records) == 1
    assert deliver_export(adapter, other) != one and len(adapter.records) == 2
