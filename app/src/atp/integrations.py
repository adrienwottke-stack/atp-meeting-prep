"""Prepared integration contracts. No live endpoint or external write is activated."""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Protocol

from .models import BookingEvent, Clarification, CRMExport


class BookingAdapter(Protocol):
    status: str

    def verify_and_normalize(self, body: bytes, headers: dict[str, str]) -> BookingEvent: ...


class CRMAdapter(Protocol):
    status: str

    def status_for(self, idempotency_key: str) -> str | None: ...
    def export(self, payload: CRMExport, idempotency_key: str) -> str: ...


@dataclass
class BookingState:
    """Pure reducer output; a live adapter must persist state+event in one transaction."""

    source: str
    booking_id: str
    revision: int = 0
    status: str = "new"
    input: dict | None = None
    event_ids: set[str] = field(default_factory=set)
    research_required: bool = False
    stale: bool = False


def reduce_booking(state: BookingState | None, event: BookingEvent) -> BookingState:
    # A rejected event must never mutate a previously persisted reducer state.
    state = deepcopy(state)
    if state is None:
        state = BookingState(event.source, event.booking_id)
    if (state.source, state.booking_id) != (event.source, event.booking_id):
        raise ValueError("BOOKING_ID_MISMATCH")
    if event.event_id in state.event_ids:
        return state
    state.event_ids.add(event.event_id)
    if event.revision <= state.revision:
        return state
    if event.kind == "created" and state.status == "cancelled":
        # An explicit new booking identity is required after cancellation.
        return state
    state.revision = event.revision
    if event.kind == "cancelled":
        state.status = "cancelled"
        state.research_required = False
    elif event.kind == "created":
        if event.meeting is None:
            raise ValueError("MEETING_REQUIRED")
        state.status = "active"
        state.input = event.meeting.model_dump(mode="json")
        state.research_required = True
    elif event.kind == "rescheduled":
        if state.status == "cancelled":
            return state
        if event.meeting is None or state.input is None:
            raise ValueError("MEETING_REQUIRED")
        updated = event.meeting.model_dump(mode="json")

        def business_fields(data):
            fields = {k: v for k, v in data.items() if k != "scheduled_at"}
            fields["people"] = sorted(
                [{k: v for k, v in p.items() if k != "participant_id"} for p in fields.get("people", [])],
                key=lambda p: (p.get("name", ""), p.get("role_hint", ""), p.get("profile_url", "")),
            )
            return fields

        old_facts = business_fields(state.input)
        new_facts = business_fields(updated)
        state.research_required = old_facts != new_facts
        state.input = updated
        state.stale = state.research_required
    return state


def crm_target(candidates: list[str]) -> tuple[str | None, Clarification | None]:
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0], None
    return None, Clarification(
        question_id="K05:target",
        code="K05",
        field="target_record_id",
        reason="Der Empfänger ist nicht eindeutig bestätigt. Nur die Übertragung wartet.",
        options=unique + ["Übertragung zurückstellen"],
        handling="blocking_transfer",
    )


def crm_key(payload: CRMExport) -> str:
    # A result version is meeting-local; meeting_id prevents cross-meeting collisions.
    return f"{payload.target_system}:{payload.target_record_id}:{payload.meeting_id}:{payload.result_version}"


def deliver_export(adapter: CRMAdapter, payload: CRMExport, *, previous_outcome="new") -> str:
    if not payload.target_record_id.strip() or not payload.approved_by:
        raise ValueError("TARGET_AND_APPROVAL_REQUIRED")
    key = crm_key(payload)
    existing = adapter.status_for(key)
    if existing:
        return existing
    if previous_outcome == "unknown":
        raise ValueError("RECONCILIATION_REQUIRED")
    return adapter.export(payload, key)


class FixtureCRMAdapter:
    """Explicit controlled contract fixture. Never used by the web/worker runtime."""

    status = "getestet (kontrollierter Adapter)"

    def __init__(self):
        self.records = {}

    def status_for(self, idempotency_key):
        return self.records.get(idempotency_key)

    def export(self, payload, idempotency_key):
        self.records.setdefault(idempotency_key, f"fixture-record-{len(self.records) + 1}")
        return self.records[idempotency_key]
