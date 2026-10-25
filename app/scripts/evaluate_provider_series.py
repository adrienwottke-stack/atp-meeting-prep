"""Authorized public-data model/research evaluation; no auth, DB, or external writes.

This exercises the provider pipeline, not the deployed application lifecycle.
Config files are read only. Secret values and raw model prompts are never logged.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from atp.budget import Budget, BudgetExceeded
from atp.clarification import plan_clarifications
from atp.config import Settings
from atp.models import MeetingInput, ResearchBundle, ResearchQuery
from atp.profiles import load_profile
from atp.providers import Provider, ProviderError, build_limited_result
from atp.rules import evaluate_icp, recommend
from atp.workflow import decorate_result

ROOT = Path(__file__).resolve().parents[1]


def public_cases():
    """Reuse existing public acceptance identifiers; no invented profile links."""
    schaeffler = {
        "company": {"name": "Schaeffler", "website": "https://www.schaeffler.de/de/"},
        "people": [
            {"participant_id": "schaeffler-person-1", "name": "Klaus Rosenfeld"},
            {"participant_id": "schaeffler-person-2", "name": "Andreas Schick"},
        ],
        "meeting_type": "setup",
    }
    trumpf = {
        "company": {"name": "TRUMPF SE + Co. KG", "website": "https://www.trumpf.com"},
        "people": [
            {"participant_id": "trumpf-person-1", "name": "Nicola Leibinger-Kammüller"},
            {"participant_id": "trumpf-person-2", "name": "Stephan Mayer"},
        ],
        "meeting_type": "audit_consultation",
    }
    return [schaeffler, trumpf, schaeffler, trumpf, schaeffler]


def evaluation_settings(config):
    settings = Settings(
        _env_file=Path(config),
        fixture_mode=False,
        app_mode="local",
        storage_backend="local",
        deadline_seconds=480,
        research_cutoff_seconds=390,
        max_model_requests=16,
        max_search_calls=24,
        max_evidence_fetches=40,
        real_data_approved=True,
    )
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY fehlt in der gewählten Konfiguration.")
    return settings


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class ObservedProvider(Provider):
    def __init__(self, settings, events, callback):
        super().__init__(settings)
        self.events, self.callback = events, callback

    async def _request(self, budget, **kwargs):
        started = perf_counter()
        event = {
            "model": kwargs["model"],
            "search": kwargs.get("search", False),
            "schema": getattr(kwargs.get("schema"), "__name__", None),
        }
        self.callback({"event": "model_request_started", **event})
        try:
            value = await super()._request(budget, **kwargs)
            event["status"] = "ok"
            return value
        except BaseException as exc:
            event.update(status="error", code=getattr(exc, "code", type(exc).__name__))
            raise
        finally:
            event["seconds"] = round(perf_counter() - started, 3)
            self.events.append(event)
            self.callback({"event": "model_request_finished", **event})


async def evaluate_case(settings, case, index, out, *, provider_factory=ObservedProvider):
    started = perf_counter()
    events, progress = [], []
    budget, profile = Budget(settings), load_profile(settings.atp_profile_version)
    data = MeetingInput.model_validate(
        {
            **case,
            "relationship": "new_contact",
            "relationship_context": "new_sales_inquiry",
            "goal": "Das Unternehmen und die beruflichen Ansprechpartner kennenlernen und einen passenden nächsten Schritt prüfen.",
            "internal_context": "",
            "confirmed": True,
            "public_identifiers_confirmed": True,
        }
    )
    query = ResearchQuery.from_input(data)
    record = {
        "index": index,
        "company": data.company.name,
        "stage": "starting",
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "provider_pipeline_only",
        "fixture_mode": False,
        "events": events,
    }

    def report(event):
        progress.append({"at": datetime.now(UTC).isoformat(), **event})
        print(
            json.dumps({"run": index, "company": data.company.name, **event}, ensure_ascii=True), flush=True
        )
        write_json(out / f"progress-{index}.json", progress)

    provider = provider_factory(settings, events, report)
    bundle, assessment, synthesis_error = ResearchBundle(), None, None

    def persist(state):
        write_json(out / f"checkpoint-{index}.json", state)
        report(
            {
                "event": "research_wave_saved",
                "waves": state.get("completed_waves"),
                "facts": len(state.get("bundle", {}).get("facts", [])),
                "model_requests": budget.usage.model_requests,
            }
        )

    async def pipeline():
        nonlocal bundle, assessment, synthesis_error
        record["stage"] = "assessing"
        assessment = await provider.assess_input(data, budget)
        write_json(out / f"assessment-{index}.json", assessment.model_dump(mode="json"))
        record["stage"] = "researching"
        bundle = await provider.research(query, budget, persist=persist)
        record["stage"] = "reviewing"
        bundle = await provider.review_evidence(query, bundle, budget)
        if assessment.limitation:
            bundle.limits.append(assessment.limitation)
        write_json(out / f"bundle-{index}.json", bundle.model_dump(mode="json"))
        record["stage"] = "synthesizing"
        icp = evaluate_icp(bundle.facts, data, profile)
        plan = plan_clarifications(data, bundle, icp, assessment=assessment)
        recommendation = recommend(
            data, icp, goal_clear=not any(q.code == "K04" for q in plan.open_questions)
        )
        meta = {
            "meeting_id": f"provider-series-{index}",
            "run_id": f"provider-series-{index}",
            "fixture_mode": False,
            "models": {
                "research": settings.model_research,
                "extraction": settings.model_extraction,
                "synthesis": settings.model_synthesis,
            },
        }
        try:
            result = await provider.synthesize(data, bundle, profile, icp, recommendation, meta, budget)
        except (ProviderError, BudgetExceeded, TimeoutError) as exc:
            synthesis_error = getattr(exc, "code", type(exc).__name__)
            result = build_limited_result(
                data,
                bundle,
                profile,
                icp,
                recommendation,
                meta,
                budget.usage,
                "Modellsynthese nicht abgeschlossen: " + synthesis_error,
            )
        result = decorate_result(result, data, bundle, icp, recommendation, plan, {})
        result.usage = budget.usage.model_copy(deep=True)
        write_json(out / f"briefing-{index}.json", result.model_dump(mode="json"))
        write_json(out / f"clarifications-{index}.json", plan.model_dump(mode="json"))
        record.update(
            status=result.status,
            quality=result.quality.model_dump(mode="json"),
            facts=len(result.facts),
            verified_facts=sum(f.status == "verified" for f in result.facts),
            historical_facts=sum(f.status == "historical" for f in result.facts),
            sources=len(result.sources),
            coverage=[c.model_dump(mode="json") for c in result.research_coverage],
            unanswered_questions=[q.model_dump(mode="json") for q in plan.open_questions],
            gaps=result.gaps,
            limits=result.limits,
            synthesis_error=synthesis_error,
            overview=[o.model_dump(mode="json") for o in result.overview],
        )

    try:
        await asyncio.wait_for(pipeline(), timeout=480)
        record["stage"] = "finished"
    except (Exception, asyncio.CancelledError) as exc:
        record.update(status="failed", error=getattr(exc, "code", type(exc).__name__))
        write_json(out / f"bundle-{index}.json", bundle.model_dump(mode="json"))
    finally:
        record.update(
            wall_seconds=round(perf_counter() - started, 3),
            usage=budget.snapshot(),
            finished_at=datetime.now(UTC).isoformat(),
        )
        write_json(out / f"run-{index}.json", record)
        report(
            {
                "event": "run_finished",
                "status": record["status"],
                "stage": record["stage"],
                "facts": record.get("facts", 0),
                "seconds": record["wall_seconds"],
            }
        )
        if hasattr(provider, "client") and hasattr(provider.client, "close"):
            await provider.client.close()
    return record


async def run_series(settings, out, *, runs=5, concurrency=2):
    semaphore = asyncio.Semaphore(min(2, max(1, concurrency)))

    async def run(index, case):
        async with semaphore:
            return await evaluate_case(settings, case, index, out)

    records = await asyncio.gather(*(run(index, case) for index, case in enumerate(public_cases()[:runs], 1)))
    report = {
        "scope": "Real public provider pipeline; no DB, hosted lifecycle, external writes, or user acceptance measured.",
        "models": {
            "research": settings.model_research,
            "extraction": settings.model_extraction,
            "synthesis": settings.model_synthesis,
        },
        "limits": {
            "seconds": 480,
            "research_seconds": 390,
            "model_requests": 16,
            "search_calls": 24,
            "evidence_fetches": 40,
        },
        "runs": records,
        "manual_source_spot_checks": "pending",
        "finished_at": datetime.now(UTC).isoformat(),
    }
    write_json(out / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--public-test-approved", action="store_true")
    parser.add_argument("--runs", type=int, default=5, choices=range(1, 6))
    parser.add_argument("--concurrency", type=int, default=2, choices=(1, 2))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.public_test_approved:
        raise SystemExit("Explicit public-data evaluation authorization required.")
    try:
        settings = evaluation_settings(args.config)
    except Exception:
        raise SystemExit(
            "Evaluation configuration unavailable or incomplete; no secret values emitted."
        ) from None
    out = args.output or ROOT / "output/provider-series" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    print("OUTPUT=" + str(out), flush=True)
    asyncio.run(run_series(settings, out, runs=args.runs, concurrency=args.concurrency))
    print("REPORT=" + str(out / "report.json"), flush=True)


if __name__ == "__main__":
    main()
