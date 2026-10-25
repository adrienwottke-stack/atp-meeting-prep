"""Bounded read-only live research check, without database or queue access."""

import argparse
import asyncio
import json
from pathlib import Path

from atp.briefing_content import build_overview
from atp.budget import Budget
from atp.config import Settings
from atp.html_export import render_html
from atp.models import Company, MeetingInput, Person, ResearchQuery
from atp.pdf import render_pdf
from atp.profiles import load_profile
from atp.providers import PUBLIC_INSTRUCTIONS, Provider, _search_profiles
from atp.research_pipeline import public_search_plan
from atp.rules import evaluate_icp, recommend


async def check(args):
    settings = Settings(_env_file=args.config)
    if not settings.openai_api_key or settings.fixture_mode:
        raise RuntimeError("Live-Recherche benötigt einen API-Zugang und FIXTURE_MODE=false.")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    data = MeetingInput(
        company=Company(name=args.company, website=args.website),
        people=[Person(participant_id="p1", name=args.person)],
        meeting_type="setup",
        relationship="new_contact",
        goal="Mögliche Zusammenarbeit und konkreten Nutzen klären",
        confirmed=True,
        public_identifiers_confirmed=True,
    )
    query, budget, provider = ResearchQuery.from_input(data), Budget(settings), Provider(settings)

    def checkpoint(snapshot):
        (output / "checkpoint.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "wave": snapshot["completed_waves"],
                    "sources": len(snapshot["sources"]),
                    "facts": len(snapshot["bundle"]["facts"]),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    previous = json.loads((output / "checkpoint.json").read_text(encoding="utf-8")) if args.resume else None
    if previous:
        budget = Budget(settings, usage=previous["usage"])
    bundle = await provider.research(query, budget, persist=checkpoint, checkpoint=previous)
    if args.refresh_profiles:
        response = await provider._request(
            budget,
            model=settings.model_research,
            search=True,
            reserve_models=3,
            instructions=PUBLIC_INSTRUCTIONS
            + "\nSuche nur das persönliche LinkedIn-Profil der genannten Person. Nutze den vollständigen Namen ohne Firmenfilter. Keine weitere Firmensuche; zitiere den gefundenen Profillink.",
            data={
                "public_query": query.model_dump(mode="json"),
                "search_queries": public_search_plan(query, people_focus=True)[:1],
            },
        )
        discoveries = _search_profiles(response, query)
        bundle.sources.extend(s for s in discoveries if not any(old.url == s.url for old in bundle.sources))
        (output / "profile-discovery.json").write_text(
            json.dumps([s.model_dump(mode="json") for s in discoveries], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"profile_candidates": [{"title": s.title, "url": s.url} for s in discoveries]},
                ensure_ascii=False,
            ),
            flush=True,
        )
    bundle = await provider.review_evidence(query, bundle, budget)
    profile = load_profile(settings.atp_profile_version)
    icp = evaluate_icp(bundle.facts, data, profile)
    recommendation = recommend(data, icp)
    result = await provider.synthesize(
        data,
        bundle,
        profile,
        icp,
        recommendation,
        {"meeting_id": "local-person-check", "run_id": "local-person-check", "fixture_mode": False},
        budget,
    )
    result.overview = build_overview(result)
    (output / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    (output / "briefing.html").write_text(render_html(result), encoding="utf-8")
    (output / "briefing.pdf").write_bytes(render_pdf(result, compact=True))
    print(
        json.dumps(
            {
                "person_fields": [
                    {"field": f.field, "value": f.value, "status": f.status}
                    for f in bundle.facts
                    if f.subject_ref == "p1"
                ],
                "sources": [s.url for s in bundle.sources],
                "usage": {
                    k: budget.snapshot()[k]
                    for k in ("model_requests", "search_calls", "evidence_fetches", "active_seconds")
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--website", required=True)
    parser.add_argument("--person", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--refresh-profiles", action="store_true")
    asyncio.run(check(parser.parse_args()))
