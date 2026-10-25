"""Shared, versioned domain contracts. Owned by the integration coordinator."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .clarification_choices import FIXED_CHOICES, FIXED_QUESTIONS


def uid() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MeetingType(StrEnum):
    SETUP = "setup"
    AUDIT = "audit_consultation"
    ENABLEMENT = "enablement_consultation"
    PROJECT = "project_review"


class Relationship(StrEnum):
    NEW = "new_contact"
    EXISTING = "existing_customer"
    PROJECT = "ongoing_project"
    UNKNOWN = "unknown"


class RecommendationValue(StrEnum):
    ATTEND = "Gespräch wahrnehmen"
    CLARIFY = "Vorab klären"
    EXCEPTION = "Ausnahme prüfen"
    DECLINE = "Absage oder Weiterleitung empfehlen"


class Company(Contract):
    name: str = Field(default="", max_length=500)
    website: str = Field(default="", max_length=2048)
    entity_hint: str = Field(default="", max_length=500)


class Person(Contract):
    participant_id: str = Field(default_factory=uid)
    name: str = Field(default="", max_length=300)
    role_hint: str = Field(default="", max_length=500)
    profile_url: str = Field(default="", max_length=2048)
    public_identifiers_confirmed: bool = False


class MeetingInput(Contract):
    schema_version: Literal[1] = 1
    company: Company
    people: list[Person] = Field(default_factory=list, max_length=10)
    meeting_type: MeetingType | None = None
    relationship: Relationship = Relationship.UNKNOWN
    relationship_context: Literal["unspecified", "existing_obligation", "new_sales_inquiry"] = "unspecified"
    goal: str = Field(default="", max_length=3000)
    internal_context: str = Field(default="", max_length=10000)
    scheduled_at: datetime | None = None
    confirmed: bool = False
    public_identifiers_confirmed: bool = False
    exception_reason: str = Field(default="", max_length=2000)
    context_ids: list[str] = Field(default_factory=list, max_length=2)

    @field_validator("scheduled_at")
    @classmethod
    def aware_time(cls, value):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Termin benötigt eine Zeitzone.")
            return value.astimezone(UTC)
        return value

    @model_validator(mode="after")
    def participant_ids_unique(self):
        if len({p.participant_id for p in self.people}) != len(self.people):
            raise ValueError("Personen benötigen unterschiedliche lokale Kennungen.")
        return self


class Clarification(Contract):
    question_id: str = Field(default_factory=uid)
    code: Literal["K01", "K02", "K03", "K04", "K05", "K06"]
    field: str
    reason: str
    options: list[str]
    handling: str
    participant_id: str | None = None
    answer: str | None = None
    question: str = ""
    affected_sections: list[str] = Field(default_factory=list)
    answer_types: list[Literal["provide", "select", "leave_unknown"]] = Field(
        default_factory=lambda: ["provide", "select", "leave_unknown"]
    )
    allow_unknown: bool = True

    @model_validator(mode="before")
    @classmethod
    def fixed_choice_contract(cls, value):
        # Apply on provider input AND persisted questions: older waiting runs
        # must offer the same choices that answer validation can accept.
        if not isinstance(value, dict) or value.get("code") != "K04":
            return value
        field = value.get("field")
        if field == "goal" and value.get("options") == ["Gesprächsziel ergänzen"]:
            # An instruction is not an answer the user can confirm as their goal.
            return {
                **value,
                "options": [],
                "answer_types": ["provide", "leave_unknown"]
                if value.get("allow_unknown", True)
                else ["provide"],
            }
        if field not in FIXED_CHOICES:
            return value
        defaults, accepted = FIXED_CHOICES[field]
        options = [option for option in value.get("options", []) if option in accepted]
        question, reason = FIXED_QUESTIONS[field]
        return {
            **value,
            "options": options or defaults,
            "answer_types": ["select", "leave_unknown"] if value.get("allow_unknown", True) else ["select"],
            "question": question,
            "reason": reason,
        }


class ClarificationAnswer(Contract):
    action: Literal["provide", "select", "leave_unknown"]
    value: str = Field(default="", max_length=3000)


class InputAssessment(Contract):
    issues: list[Clarification] = Field(default_factory=list)
    evaluated: bool = False
    limitation: str = ""


class EvidenceVerdict(Contract):
    fact_id: str
    verdict: Literal["supported", "contradicted", "unclear"]
    reason: str


class ClarificationPlan(Contract):
    questions: list[Clarification] = Field(default_factory=list, max_length=3)
    open_questions: list[Clarification] = Field(default_factory=list)
    withheld_sections: list[str] = Field(default_factory=list)
    exhausted: bool = False


class QualityAssessment(Contract):
    state: Literal["not_assessed", "sufficient", "limited", "clarification_required"] = "not_assessed"
    reasons: list[str] = Field(default_factory=list)


class OverviewItem(Contract):
    category: Literal["company", "development", "people", "opener", "next_step"]
    text: str
    kind: Literal["fact", "user_confirmed", "hypothesis", "unknown"] = "unknown"
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchCoverage(Contract):
    area: Literal["company", "developments", "people", "icp"]
    status: Literal["covered", "partial", "missing"]
    reasons: list[str] = Field(default_factory=list)


class Source(Contract):
    source_id: str = Field(default_factory=uid)
    url: str
    title: str
    retrieved_at: datetime = Field(default_factory=utcnow)
    content_hash: str = ""
    excerpt: str = Field(default="", max_length=12000)
    information_as_of: str | None = None
    published_at: str | None = None
    verified: bool = False
    limitation: str | None = None
    discovered_urls: list[str] = Field(default_factory=list, max_length=12)
    retrieval_kind: Literal["direct", "search_index"] = "direct"
    discovered_for: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("url")
    @classmethod
    def safe_source_url(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("Quellen benötigen eine HTTP(S)-URL ohne Zugangsdaten.")
        if any(ord(c) < 33 for c in value) or "\\" in value:
            raise ValueError("Ungültige Quellen-URL.")
        return value


class Fact(Contract):
    fact_id: str = Field(default_factory=uid)
    subject_ref: str = "company"
    field: str
    value: Any = None
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["verified", "unknown", "conflicting", "historical"] = "unknown"
    provenance: Literal["web_verified", "user_confirmed", "unknown"] = "unknown"
    information_as_of: str | None = None
    supporting_quote: str = ""
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    conflict: str | None = None


class Criterion(Contract):
    field: str
    label: str
    value: Any = None
    status: Literal["matching", "deviating", "unclear"] = "unclear"
    subject_ref: str = "company"
    evidence_ids: list[str] = Field(default_factory=list)
    provenance: str = "unknown"
    information_as_of: str | None = None
    deviation_kind: Literal["corridor", "target_structure", "exclusion"] | None = None
    reason: str


class ICPResult(Contract):
    status: Literal["matching", "deviating", "unclear"]
    criteria: list[Criterion]
    deviation_kinds: list[str] = Field(default_factory=list)
    entry_service_ids: list[str] = Field(default_factory=list)
    entry_reason: str = ""
    open_points: list[str] = Field(default_factory=list)


class Recommendation(Contract):
    value: RecommendationValue
    reason: str
    rule: str


class Claim(Contract):
    text: str
    kind: Literal["fact", "user_confirmed", "hypothesis", "unknown"] = "unknown"
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class PersonProfile(Contract):
    participant_id: str
    name: str
    status: Literal["confirmed", "ambiguous", "unresolved"] = "unresolved"
    role: Claim
    career: list[Claim] = Field(default_factory=list)
    topics: list[Claim] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class Opportunity(Contract):
    observation: str
    evidence_ids: list[str] = Field(default_factory=list)
    hypothesis: str
    service_id: str
    validation_question: str


class Question(Contract):
    text: str
    purpose: str
    reference: str


class StoryPhase(Contract):
    phase: Literal["Einstieg", "Ausgangslage und Ziel", "Vertiefung", "Nächster Schritt"]
    goal: str
    impulse: str


class Usage(Contract):
    model_requests: int = 0
    search_calls: int = 0
    observed_search_calls: int = 0
    search_calls_unknown_attempts: int = 0
    search_trace: list[dict[str, Any]] = Field(default_factory=list)
    evidence_fetches: int = 0
    pdf_fetches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    active_seconds: float = 0
    phase_seconds: dict[str, float] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class BriefingResult(Contract):
    schema_version: Literal[1] = 1
    briefing_id: str = Field(default_factory=uid)
    meeting_id: str
    run_id: str
    result_version: int
    status: Literal["completed", "partial"]
    generated_at: datetime = Field(default_factory=utcnow)
    profile_version: str
    profile_hash: str
    prompt_version: str = "atp-prompts-1"
    rule_version: str = "atp-rules-1"
    models: dict[str, str] = Field(default_factory=dict)
    fixture_mode: bool = False
    input: MeetingInput
    summary: list[str] = Field(max_length=5)
    company: list[Claim]
    people: list[PersonProfile]
    opportunities: list[Opportunity]
    questions: list[Question] = Field(min_length=5)
    storyline: list[StoryPhase] = Field(min_length=4, max_length=4)
    icp: ICPResult
    recommendation: Recommendation
    sources: list[Source]
    facts: list[Fact]
    gaps: list[str]
    limits: list[str]
    usage: Usage = Field(default_factory=Usage)
    memory: dict = Field(default_factory=dict)
    quality: QualityAssessment = Field(default_factory=QualityAssessment)
    clarification_version: str = "legacy"
    resolutions: dict[str, Any] = Field(default_factory=dict)
    overview: list[OverviewItem] = Field(default_factory=list, max_length=5)
    research_coverage: list[ResearchCoverage] = Field(default_factory=list)
    presentation_version: str = "legacy"

    @model_validator(mode="after")
    def references_valid(self):
        sources = {s.source_id for s in self.sources}
        facts = {f.fact_id for f in self.facts}
        verified_sources = {s.source_id for s in self.sources if s.verified}
        fact_map = {f.fact_id: f for f in self.facts}
        if len(sources) != len(self.sources) or len(facts) != len(self.facts):
            raise ValueError("Quellen- und Faktenkennungen müssen eindeutig sein.")
        if len({p.phase for p in self.storyline}) != 4:
            raise ValueError("Vier unterschiedliche Gesprächsphasen erforderlich.")
        if len({q.text.strip().casefold() for q in self.questions}) < 5:
            raise ValueError("Mindestens fünf unterschiedliche Fragen erforderlich.")
        if {p.participant_id for p in self.people} != {p.participant_id for p in self.input.people}:
            raise ValueError("Jede eingegebene Person benötigt genau ein Profil.")
        for f in self.facts:
            if not set(f.evidence_ids) <= sources:
                raise ValueError("Unbekannter Quellenverweis.")
            if f.status == "verified" and f.provenance == "web_verified" and not f.evidence_ids:
                raise ValueError("Web-Tatsache ohne Beleg.")
            if (
                f.provenance == "web_verified"
                and f.status in ("verified", "historical")
                and not set(f.evidence_ids) <= verified_sources
            ):
                raise ValueError("Eine Tatsache verweist auf ungeprüfte Quellen.")
            if (
                f.status == "verified"
                and f.provenance == "user_confirmed"
                and not (f.confirmed_by and f.confirmed_at)
            ):
                raise ValueError("Bestätigte Tatsache ohne bestätigenden Nutzer/Zeitpunkt.")
        claims = list(self.company)
        for p in self.people:
            claims.extend([p.role, *p.career, *p.topics])
        for c in claims:
            if not set(c.evidence_ids) <= sources or not set(c.fact_ids) <= facts:
                raise ValueError("Aussage mit unbekanntem Verweis.")
            if c.kind == "fact" and not c.fact_ids:
                raise ValueError("Tatsachenaussage benötigt geprüfte Fakten.")
            if c.kind == "fact" and any(
                fact_map[i].status not in ("verified", "historical") for i in c.fact_ids
            ):
                raise ValueError("Tatsachenaussage verweist auf ungeklärte oder widersprüchliche Fakten.")
        for criterion in self.icp.criteria:
            if not set(criterion.evidence_ids) <= sources:
                raise ValueError("ICP-Kriterium mit unbekanntem Quellenverweis.")
        for o in self.opportunities:
            if not set(o.evidence_ids) <= sources:
                raise ValueError("Ansatz mit unbekanntem Beleg.")
        if len({o.category for o in self.overview}) != len(self.overview):
            raise ValueError("Überblickskategorien müssen eindeutig sein.")
        for item in self.overview:
            if not set(item.fact_ids) <= facts or not set(item.evidence_ids) <= sources:
                raise ValueError("Überblick mit unbekanntem Verweis.")
            if item.kind == "fact" and (
                not item.fact_ids
                or any(fact_map[i].status not in ("verified", "historical") for i in item.fact_ids)
            ):
                raise ValueError("Überblickstatsache benötigt geprüfte Fakten.")
        return self


class ResearchQuery(Contract):
    company_name: str
    domain: str
    people: list[dict[str, str]] = Field(default_factory=list)

    @classmethod
    def from_input(cls, data: MeetingInput):
        if not data.public_identifiers_confirmed:
            raise ValueError("Öffentliche Identifikatoren müssen freigegeben sein.")
        return cls(
            company_name=data.company.name,
            domain=urlsplit(data.company.website).hostname or "",
            people=[
                {
                    "participant_id": p.participant_id,
                    "name": p.name,
                    "profile_url": p.profile_url if p.public_identifiers_confirmed else "",
                    "role_hint": p.role_hint if p.public_identifiers_confirmed else "",
                }
                for p in data.people
            ],
        )


class ResearchBundle(Contract):
    sources: list[Source] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    evidence_review: list[EvidenceVerdict] = Field(default_factory=list)
    supplemental_search_performed: bool = False
    research_coverage: list[ResearchCoverage] = Field(default_factory=list)


class Actor(Contract):
    user_id: str
    org_id: str
    role: str = "member"
    token: str = Field(default="", exclude=True)


class Decision(Contract):
    original_recommendation: RecommendationValue
    confirmed_decision: RecommendationValue
    decided_by: str
    decided_at: datetime = Field(default_factory=utcnow)
    override_reason: str = ""


class BookingEvent(Contract):
    schema_version: Literal[1] = 1
    source: str
    event_id: str
    booking_id: str
    revision: int = Field(ge=1)
    occurred_at: datetime
    kind: Literal["created", "rescheduled", "cancelled"]
    meeting: MeetingInput | None = None


class CRMExport(Contract):
    schema_version: Literal[1] = 1
    meeting_id: str
    result_version: int
    target_system: str
    target_record_id: str
    approved_by: str
    approved_at: datetime
    summary: list[str]
    icp: ICPResult
    decision: Decision
    protected_url: str
