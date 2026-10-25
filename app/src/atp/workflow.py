"""Explicit resumable pipeline, entirely independent of the HTTP process."""

import asyncio
import inspect
import logging
from copy import deepcopy
from datetime import date
from time import monotonic

from .answers import apply_confirmed_claims
from .budget import Budget, BudgetExceeded, LeaseLost
from .clarification import CLARIFICATION_VERSION, answer_left_unknown, plan_clarifications
from .gap_reconciliation import reconcile_gaps
from .job_support import LeaseHeartbeat
from .models import (
    BriefingResult,
    Clarification,
    InputAssessment,
    MeetingInput,
    ResearchBundle,
    ResearchQuery,
)
from .profiles import profile_hash
from .providers import PROMPT_VERSION, FixtureProvider, Provider, ProviderError
from .quality import assess_quality
from .research_pipeline import retain_unprocessed_sources
from .rules import clarify_input, evaluate_icp, recommend

log = logging.getLogger(__name__)


def decorate_result(result, data, bundle, icp, recommendation, plan, answers):
    """Attach shared usability/provenance/overview to every final or interim result."""
    from .briefing_content import PRESENTATION_VERSION, build_overview
    from .research_coverage import build_coverage

    result.quality = assess_quality(
        data, bundle, icp, recommendation, open_questions=plan.open_questions, answers=answers
    )
    result.resolutions = answers
    result.clarification_version = CLARIFICATION_VERSION
    # Supplementary career/knowledge gaps affect usability, not completion.
    material_gap = (
        result.quality.state == "clarification_required"
        or any(q.code == "K06" for q in plan.open_questions)
        or not any(f.subject_ref == "company" and f.status == "verified" for f in bundle.facts)
    )
    limited_processing = bool(bundle.limits) or any("Begrenzter Abschluss:" in note for note in result.limits)
    result.status = "partial" if material_gap or limited_processing else "completed"
    if "opportunities" in plan.withheld_sections:
        result.opportunities = []
    result.research_coverage = build_coverage(bundle)
    result.presentation_version = PRESENTATION_VERSION
    fallback = build_overview(result)
    authored = {item.category: item for item in result.overview}
    result.overview = [
        authored.get(item.category, item) if item.category not in ("people", "next_step") else item
        for item in fallback
    ]
    result.summary = [item.text for item in result.overview]
    result.rule_version = "atp-rules-2"
    return result


class Workflow:
    def __init__(self, settings, repository, storage):
        self.settings = settings
        self.repo = repository
        self.storage = storage
        self.provider = FixtureProvider(settings) if settings.fixture_mode else Provider(settings)

    async def process(self, job):
        checkpoint = dict(job.get("checkpoint") or {})
        phase = checkpoint.get("phase", "researching")
        jid, token = job["id"], job["lease_token"]
        heartbeat = LeaseHeartbeat(lambda: self.repo.heartbeat(jid, token), self.settings.lease_seconds)
        lost = heartbeat.lost

        def save(usage):
            if lost.is_set():
                raise LeaseLost("Lease verloren.")
            ok = self.repo.checkpoint(jid, token, phase, checkpoint, usage)
            if not ok:
                lost.set()
                raise LeaseLost("Lease verloren.")
            if "provider_insufficient_quota" in usage.get("errors", []):
                raise ProviderError("provider_insufficient_quota")
            return True

        budget = Budget(self.settings, job.get("usage"), persist=save)

        heartbeat.start()
        bundle = None
        try:
            data = MeetingInput.model_validate(job["input"])
            profile = job["profile"]
            memory = job.get("memory_snapshot", {})
            if memory:
                from .models import Actor

                memory_actor = Actor(user_id=job["created_by"], org_id=str(job["org_id"]))
                self.repo.memory().validate_snapshot(memory_actor, memory)
            if not profile or not profile.get("services"):
                raise ValueError("NO_ACTIVE_PROFILE")
            answers = job.get("answers") or checkpoint.get("answers") or {}
            checkpoint["answers"] = answers
            input_questions = clarify_input(data)
            blocked_company = [
                q for q in input_questions if q.code in ("K01", "K02") and q.question_id not in answers
            ]
            if blocked_company:
                for question in blocked_company:
                    question.allow_unknown = question.code != "K01"
                    question.answer_types = (
                        ["provide"] if question.code == "K01" else ["select", "leave_unknown"]
                    )
                self.repo.pause_job(
                    jid,
                    token,
                    [q.model_dump(mode="json") for q in blocked_company[:3]],
                    checkpoint,
                    budget.snapshot(),
                )
                return
            company_unknown = any(
                q.code == "K02" and answer_left_unknown(answers.get(q.question_id)) for q in input_questions
            )
            if "input_assessment" not in checkpoint:
                try:
                    assessment = (
                        await self.provider.assess_input(data, budget)
                        if not company_unknown
                        else InputAssessment()
                    )
                except (ProviderError, TimeoutError, BudgetExceeded):
                    assessment = InputAssessment(
                        limitation="Inhaltliche Auftragsprüfung nicht verfügbar; Angaben bleiben vorsichtig eingeordnet."
                    )
                checkpoint["input_assessment"] = assessment.model_dump(mode="json")
                save(budget.snapshot())
            assessment = InputAssessment.model_validate(checkpoint["input_assessment"])
            if company_unknown:
                bundle = ResearchBundle(
                    gaps=[
                        "Firmenzuordnung bewusst offengelassen; keine firmenbezogene Recherche oder Tatsachenbehauptung."
                    ]
                )
            elif "research" in checkpoint:
                bundle = ResearchBundle.model_validate(checkpoint["research"])
            else:
                phase = "researching"
                checkpoint["phase"] = phase
                save(budget.snapshot())
                started = monotonic()
                research_query = ResearchQuery.from_input(data)
                kwargs = {}
                if "checkpoint" in inspect.signature(self.provider.research).parameters:

                    def persist_research(progress):
                        # Provider work on the next wave must not mutate the
                        # last complete snapshot retained by this worker.
                        checkpoint["research_progress"] = deepcopy(progress)
                        if (
                            isinstance(progress, dict)
                            and progress.get("version") == 2
                            and progress.get("public_query") == research_query.model_dump(mode="json")
                            and isinstance(progress.get("bundle"), dict)
                        ):
                            try:
                                completed = ResearchBundle.model_validate(progress["bundle"])
                            except ValueError:
                                pass
                            else:
                                checkpoint["research_last_complete_progress"] = {
                                    "version": 2,
                                    "public_query": research_query.model_dump(mode="json"),
                                    "bundle": completed.model_dump(mode="json"),
                                    "limits": [v for v in progress.get("limits", []) if isinstance(v, str)],
                                }
                        save(budget.snapshot())

                    kwargs = {"checkpoint": checkpoint.get("research_progress"), "persist": persist_research}
                try:
                    bundle = await asyncio.wait_for(
                        self.provider.research(research_query, budget, **kwargs),
                        timeout=budget.timeout(
                            max(1, self.settings.research_cutoff_seconds - budget.elapsed)
                        ),
                    )
                except (TimeoutError, BudgetExceeded):
                    if budget.remaining_seconds < 0.05:
                        raise
                    progress = (
                        checkpoint.get("research_last_complete_progress")
                        or checkpoint.get("research_progress")
                        or {}
                    )
                    last_complete = (
                        progress.get("bundle")
                        if isinstance(progress, dict)
                        and progress.get("version") == 2
                        and progress.get("public_query") == research_query.model_dump(mode="json")
                        else None
                    )
                    try:
                        bundle = (
                            ResearchBundle.model_validate(last_complete)
                            if last_complete
                            else ResearchBundle()
                        )
                    except ValueError:
                        bundle = ResearchBundle()
                    # Keep discovered/fetched sources from the unfinished wave
                    # visible without promoting their unreviewed text to facts.
                    latest = checkpoint.get("research_progress") or {}
                    if (
                        isinstance(latest, dict)
                        and latest.get("version") == 2
                        and latest.get("public_query") == research_query.model_dump(mode="json")
                    ):
                        retain_unprocessed_sources(bundle, latest.get("sources", []))
                    checkpoint["evidence_reviewed"] = False
                    checkpoint.pop("result", None)
                    for saved_progress in (progress, checkpoint.get("research_progress")):
                        if (
                            isinstance(saved_progress, dict)
                            and saved_progress.get("version") == 2
                            and saved_progress.get("public_query") == research_query.model_dump(mode="json")
                        ):
                            bundle.limits = list(
                                dict.fromkeys(
                                    [
                                        *bundle.limits,
                                        *[v for v in saved_progress.get("limits", []) if isinstance(v, str)],
                                    ]
                                )
                            )
                    bundle.limits.append(
                        "Recherchezeitfenster oder Recherchebudget erreicht; letzter vollständig gespeicherter "
                        "Recherchestand wird vor dem begrenzten Abschluss geprüft."
                    )
                    if not bundle.facts:
                        bundle.gaps.append(
                            "Keine vollständig gespeicherten Recherchefakten verfügbar; Angaben bleiben offen."
                        )
                budget.usage.phase_seconds["researching"] = monotonic() - started
                checkpoint["research"] = bundle.model_dump(mode="json")
                save(budget.snapshot())
            if not checkpoint.get("evidence_reviewed") and not company_unknown:
                try:
                    bundle = await asyncio.wait_for(
                        self.provider.review_evidence(ResearchQuery.from_input(data), bundle, budget),
                        timeout=budget.timeout(max(0.05, min(30, budget.remaining_seconds - 60))),
                    )
                except (ProviderError, TimeoutError, BudgetExceeded):
                    bundle = bundle.model_copy(deep=True)
                    for fact in bundle.facts:
                        if fact.provenance == "web_verified":
                            fact.status = "unknown"
                    bundle.limits.append(
                        "Semantische Belegprüfung nicht abgeschlossen; betroffene Angaben nicht als verifiziert übernommen."
                    )
                checkpoint["evidence_reviewed"] = True
                checkpoint["research"] = bundle.model_dump(mode="json")
                save(budget.snapshot())
            phase = "evaluating"
            checkpoint["phase"] = phase
            if assessment.limitation and assessment.limitation not in bundle.limits:
                bundle.limits.append(assessment.limitation)
            # Keep original questions for skipped identities and quality/provenance.
            originals = {q.question_id: q.model_dump(mode="json") for q in bundle.clarifications}
            originals = {**checkpoint.get("identity_questions", {}), **originals}
            checkpoint["identity_questions"] = originals
            if answers:
                from .rules import apply_research_answers

                bundle, data = apply_research_answers(bundle, data, answers)
                for qid, raw in originals.items():
                    if answer_left_unknown(answers.get(qid)) and not any(
                        q.question_id == qid for q in bundle.clarifications
                    ):
                        bundle.clarifications.append(Clarification.model_validate(raw))
                if not company_unknown:
                    bundle = apply_confirmed_claims(bundle, answers)
                # URL hints are evidence only after the normal safe fetch/extract/review path.
                processed_urls = set(checkpoint.get("processed_answer_urls", []))
                urls = [
                    r["value"]
                    for r in answers.values()
                    if isinstance(r, dict)
                    and r.get("action") == "provide"
                    and r.get("value", "").startswith(("https://", "http://"))
                    and r["value"] not in processed_urls
                ]
                if urls and not company_unknown and hasattr(self.provider, "research_evidence_urls"):
                    try:
                        extra = await self.provider.research_evidence_urls(
                            ResearchQuery.from_input(data), urls, budget
                        )
                        extra = await self.provider.review_evidence(
                            ResearchQuery.from_input(data), extra, budget
                        )
                        known_sources = {s.source_id for s in bundle.sources}
                        known_facts = {f.fact_id for f in bundle.facts}
                        bundle.sources.extend(s for s in extra.sources if s.source_id not in known_sources)
                        bundle.facts.extend(f for f in extra.facts if f.fact_id not in known_facts)
                        bundle.gaps.extend(extra.gaps)
                        bundle.limits.extend(extra.limits)
                    except (ProviderError, TimeoutError, BudgetExceeded):
                        bundle.limits.append(
                            "Nachgereichter öffentlicher Beleg konnte nicht abschließend geprüft werden."
                        )
                    checkpoint["processed_answer_urls"] = sorted(processed_urls | set(urls))
                checkpoint["research"] = bundle.model_dump(mode="json")
            if not self.settings.fixture_mode:
                from .role_currency import enforce_current_role_currency

                current = enforce_current_role_currency(bundle, date.today())
                if current.facts != bundle.facts:
                    checkpoint.pop("result", None)
                bundle = current
            # Resumable jobs must not publish pre-policy prose from older workers.
            if checkpoint.get("result") and checkpoint["result"].get("prompt_version") != PROMPT_VERSION:
                checkpoint.pop("result", None)
            bundle = reconcile_gaps(bundle, data)
            checkpoint["research"] = bundle.model_dump(mode="json")
            icp = evaluate_icp(bundle.facts, data, profile)
            rounds = checkpoint.get("clarification_rounds", 0)
            plan = plan_clarifications(
                data, bundle, icp, assessment=assessment, answers=answers, rounds=rounds
            )
            recommendation = recommend(
                data,
                icp,
                company_clear=not company_unknown,
                goal_clear=not any(q.code == "K04" for q in plan.open_questions),
            )
            checkpoint["clarification_plan"] = plan.model_dump(mode="json")
            if plan.questions:
                from .providers import build_limited_result

                preview_meta = {
                    "meeting_id": job["meeting_id"],
                    "run_id": job["run_id"],
                    "result_version": job.get("result_version", 1),
                    "fixture_mode": self.settings.fixture_mode,
                }
                preview = build_limited_result(
                    data,
                    bundle,
                    profile,
                    icp,
                    recommendation,
                    preview_meta,
                    budget.usage,
                    "Rückfrage offen; unabhängiger Zwischenstand.",
                )
                decorate_result(preview, data, bundle, icp, recommendation, plan, answers)
                checkpoint["independent_result"] = preview.model_dump(mode="json")
                checkpoint["questions"] = [q.model_dump(mode="json") for q in plan.open_questions]
                checkpoint["clarification_rounds"] = rounds + 1
                self.repo.pause_job(
                    jid,
                    token,
                    [q.model_dump(mode="json") for q in plan.questions],
                    checkpoint,
                    budget.snapshot(),
                )
                return
            save(budget.snapshot())
            meta = {
                "meeting_id": job["meeting_id"],
                "run_id": job["run_id"],
                "result_version": job.get("result_version", 1),
                "profile_version": profile["profile_id"],
                "profile_hash": profile_hash(profile),
                "models": {
                    "extraction": self.settings.model_extraction,
                    "research": self.settings.model_research,
                    "review": self.settings.model_review,
                    "synthesis": self.settings.model_synthesis,
                },
                "fixture_mode": self.settings.fixture_mode,
                "memory": memory,
            }
            if checkpoint.get("result"):
                result = BriefingResult.model_validate(checkpoint["result"])
            else:
                phase = "generating"
                checkpoint["phase"] = phase
                save(budget.snapshot())
                started = monotonic()
                try:
                    if budget.remaining_seconds <= 20:
                        raise BudgetExceeded("Abschlusszeit für Export und Speicherung reserviert.")
                    result = await asyncio.wait_for(
                        self.provider.synthesize(data, bundle, profile, icp, recommendation, meta, budget),
                        timeout=budget.timeout(min(40, budget.remaining_seconds - 20)),
                    )
                except (BudgetExceeded, TimeoutError, ProviderError) as exc:
                    from .providers import build_limited_result

                    result = build_limited_result(
                        data, bundle, profile, icp, recommendation, meta, budget.usage, type(exc).__name__
                    )
                budget.usage.phase_seconds["generating"] = monotonic() - started
                # Deterministic evaluation cannot be changed by synthesis.
                result.icp = icp
                result.memory = memory
                result.recommendation = recommendation
                services = {s["id"] for s in profile["services"]}
                if any(o.service_id not in services for o in result.opportunities):
                    raise ValueError("INVALID_SERVICE_REFERENCE")
                result.usage = budget.usage
                decorate_result(result, data, bundle, icp, recommendation, plan, answers)
                checkpoint["result"] = result.model_dump(mode="json")
                save(budget.snapshot())
            phase = "exporting"
            if memory:
                self.repo.memory().validate_snapshot(memory_actor, memory)
            checkpoint["phase"] = phase
            save(budget.snapshot())
            result.usage = budget.usage
            content, pdf_error = None, None
            try:
                from .pdf import render_pdf

                started = monotonic()
                result.usage.active_seconds = budget.elapsed
                content = await asyncio.wait_for(
                    asyncio.to_thread(render_pdf, result), timeout=budget.timeout(30)
                )
                budget.usage.phase_seconds["exporting"] = monotonic() - started
            except Exception as exc:
                pdf_error = "PDF_EXPORT_FAILED"
                log.warning("pdf_failed run=%s code=%s", job["run_id"], type(exc).__name__)

            def publish():
                # All writers, stale-worker cleanup and purge share this cross-process lock.
                # Recheck authority AFTER acquiring it, because deletion may already be complete.
                with self.repo.storage_lock(job["meeting_id"], timeout=budget.timeout(5)):
                    if lost.is_set() or not self.repo.heartbeat(jid, token):
                        raise LeaseLost("Lease verloren.")
                    pdf_path, export_error = None, pdf_error

                    def cleanup(path):
                        try:
                            self.storage.delete_pdf(path)
                        except Exception:
                            self.repo.enqueue_storage_cleanup(
                                path, job["org_id"], job["meeting_id"], job["run_id"]
                            )

                    try:
                        if content is not None:
                            try:
                                pdf_path = self.storage.put_pdf(
                                    job["org_id"],
                                    job["meeting_id"],
                                    job["run_id"],
                                    content,
                                    timeout=budget.timeout(20),
                                )
                            except Exception:
                                export_error = "PDF_EXPORT_FAILED"
                                # A failed/uncertain upload cannot discard a valid briefing.
                                cleanup(f"{job['org_id']}/{job['meeting_id']}/{job['run_id']}/result.pdf")
                        if budget.remaining_seconds <= 0:
                            result.status = "partial"
                            result.limits.append(
                                "Zeitbudget beim Export erschöpft; Ergebnis kontrolliert gespeichert."
                            )
                        result.usage.active_seconds = budget.elapsed
                        valid = BriefingResult.model_validate(result.model_dump(mode="json"))
                        committed = self.repo.finish_job(
                            jid, token, valid.model_dump(mode="json"), pdf_path, export_error
                        )
                        if not committed:
                            raise LeaseLost("Ergebnis wurde nicht übernommen.")
                    except Exception:
                        # Includes files that a timed-out upload may already have created.
                        path = pdf_path or f"{job['org_id']}/{job['meeting_id']}/{job['run_id']}/result.pdf"
                        cleanup(path)
                        raise

            await asyncio.wait_for(asyncio.to_thread(publish), timeout=budget.timeout(25))
        except LeaseLost:
            log.info("lease_lost run=%s", job["run_id"])
        except Exception as exc:
            # Provider messages may contain source data or keys: persist only stable error codes.
            code = (
                "BUDGET_EXHAUSTED" if isinstance(exc, (BudgetExceeded, TimeoutError)) else "PROCESSING_FAILED"
            )
            if isinstance(exc, ValueError) and str(exc) in ("MEMORY_CHANGED", "MEMORY_DISABLED"):
                code = str(exc)
            if isinstance(exc, ProviderError) and exc.code in {
                "search_cap_not_confirmed",
                "search_cap_unsupported_sdk",
                "search_cap_or_model_rejected",
                "provider_incomplete",
                "provider_request_failed",
                "provider_insufficient_quota",
                "provider_schema_invalid",
            }:
                code = exc.code.upper()
            log.warning("run_failed run=%s code=%s type=%s", job["run_id"], code, type(exc).__name__)
            try:
                if not lost.is_set():
                    self.repo.checkpoint(jid, token, phase, checkpoint, budget.snapshot())
                    self.repo.fail_job(jid, token, code)
            except Exception:
                log.warning("failure_checkpoint_unavailable run=%s", job["run_id"])
        finally:
            heartbeat.close()

    def purge_once(self):
        deletion = self.repo.claim_deletion()
        if not deletion:
            return False
        with self.repo.storage_lock(deletion["meeting_id"]):
            self.storage.delete_meeting(deletion["org_id"], deletion["meeting_id"])
            self.repo.finish_deletion(deletion["meeting_id"])
        return True

    def cleanup_once(self):
        pending = self.repo.pending_storage_cleanup()
        if not pending:
            return False
        with self.repo.storage_lock(pending["meeting_id"]):
            if not self.repo.pdf_path_in_use(pending["path"]):
                try:
                    self.storage.delete_pdf(pending["path"])
                except Exception:
                    self.repo.defer_storage_cleanup(pending["path"])
                    return False
            self.repo.finish_storage_cleanup(pending["path"])
        return True
