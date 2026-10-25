"""Portable memory contracts, independent of database and model vendor."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field, field_validator

from .models import Actor, Contract


class ContextInput(Contract):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["company", "project", "general"]
    description: str = Field(default="", max_length=3000)
    external_id: str = Field(default="", max_length=200)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Name fehlt.")
        return value.strip()


class EntryInput(Contract):
    text: str = Field(min_length=1, max_length=2000)
    kind: Literal["fact", "decision", "goal", "question", "learning"] = "fact"
    source: str = Field(default="Manuelle Eingabe", min_length=1, max_length=1000)
    information_as_of: str = Field(default="", max_length=40)
    status: Literal["draft", "confirmed"] = "draft"
    applies_to: list[Literal["setup", "audit_consultation", "enablement_consultation", "project_review"]] = (
        Field(default_factory=list, max_length=4)
    )
    expected_version: int | None = Field(default=None, ge=1)


class FeedbackInput(Contract):
    context_id: UUID
    text: str = Field(min_length=1, max_length=3000)
    kind: Literal["correction", "helpful", "unhelpful", "outcome"] = "correction"
    reference: str = Field(default="", max_length=1000)
    briefing_id: UUID | None = None


class ProposalInput(Contract):
    feedback_id: UUID
    target_context_id: UUID


class BriefingFeedbackInput(Contract):
    submission_id: UUID
    text: str = Field(min_length=1, max_length=1600)
    kind: Literal["correction", "helpful", "unhelpful", "outcome"] = "correction"
    reference: str = Field(default="", max_length=300)
    remember: bool = False

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Bitte eine Korrektur oder ein Gesprächsergebnis eintragen.")
        return value.strip()


class LearningDraft(Contract):
    text: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=1000)


class ShareInput(Contract):
    user_id: UUID
    permission: Literal["read", "edit"] = "read"


class ImportInput(Contract):
    package: dict
    apply: bool = False
    # Source context UUID -> explicitly chosen existing destination context UUID.
    mapping: dict[UUID, UUID] = Field(default_factory=dict)


class MemoryStore(Protocol):
    def context(self, actor: Actor, context_id: str, db=None, query: str = "") -> dict: ...
    def save_context(self, actor: Actor, value: ContextInput, context_id: str | None = None) -> dict: ...
    def share(self, actor: Actor, context_id: str, user_id: str, permission: str | None = None) -> None: ...
    def contexts(self, actor: Actor) -> list[dict]: ...
    def snapshot(
        self,
        actor: Actor,
        context_ids: list[str],
        meeting_type: str,
        db=None,
        query: str = "",
        company: dict | None = None,
    ) -> dict: ...
    def validate_snapshot(self, actor: Actor, snapshot: dict, db=None) -> None: ...
    def save_entry(
        self, actor: Actor, context_id: str, value: EntryInput, entry_id: str | None = None
    ) -> dict: ...
    def feedback(self, actor: Actor, value: FeedbackInput) -> dict: ...
    def confirm_learning(self, actor: Actor, task_id: str, value: EntryInput) -> dict: ...
    def export(self, actor: Actor, ids: list[str]) -> dict: ...
    def import_package(
        self, actor: Actor, package: dict, mapping: dict[str, str], apply: bool = False
    ) -> dict: ...
    def invalidate(self, actor: Actor, context_id: str, entry_id: str | None = None) -> None: ...
    def start_task(self, actor: Actor, kind: str, input_data: dict, key: str) -> dict: ...
    def tasks(self, actor: Actor, task_id: str | None = None) -> list[dict] | dict: ...
    def link(
        self, db, snapshot: dict, *, company_run_id: str | None = None, task_id: str | None = None
    ) -> None: ...
