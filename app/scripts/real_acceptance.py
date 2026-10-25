"""Explicitly authorized public-data acceptance; never changes the demo .env.

Run without a concurrent ATP worker. Uses real Auth/API/DB/Provider/PDF paths.
Only this script's meetings/drafts are removed after copying audit artifacts.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient

from atp.config import Settings
from atp.models import MeetingInput
from atp.providers import Provider
from atp.web import create_app
from atp.workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {
        "company": {"name": "item Industrietechnik GmbH", "website": "https://www.item24.com"},
        "people": [{"name": "Heiner Giese"}, {"name": "Thomas Neller"}],
        "meeting_type": "setup",
    },
    {
        "company": {"name": "TRUMPF SE + Co. KG", "website": "https://www.trumpf.com"},
        "people": [{"name": "Nicola Leibinger-Kammüller"}, {"name": "Stephan Mayer"}],
        "meeting_type": "audit_consultation",
    },
]


class ObservedProvider(Provider):
    def __init__(self, settings, events):
        super().__init__(settings)
        self.events = events

    async def _request(self, budget, **kwargs):
        started = perf_counter()
        event = {
            "model": kwargs["model"],
            "search": kwargs.get("search", False),
            "schema": getattr(kwargs.get("schema"), "__name__", None),
        }
        try:
            result = await super()._request(budget, **kwargs)
            event["status"] = "ok"
            return result
        except Exception as exc:
            event.update(status="error", code=getattr(exc, "code", type(exc).__name__))
            raise
        finally:
            event["seconds"] = round(perf_counter() - started, 3)
            self.events.append(event)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-test-approved", action="store_true")
    parser.add_argument("--runs", type=int, default=5, choices=range(1, 6))
    parser.add_argument("--case", choices=("alternating", "item", "trumpf"), default="alternating")
    args = parser.parse_args()
    if not args.public_test_approved:
        raise SystemExit("Explicit public-data test authorization required.")
    original = Settings()
    if original.app_mode != "local" or "127.0.0.1:55432" not in original.database_url:
        raise SystemExit("Only the isolated local ATP stack is permitted.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "output" / "real-acceptance" / stamp
    out.mkdir(parents=True)
    # A one-day upper bound is scoped to disposable test records, not ATP production policy.
    settings = Settings(fixture_mode=False, real_data_approved=True, retention_days=1)
    if settings.setup_issues():
        raise SystemExit("Real-test configuration incomplete.")
    app = create_app(settings)
    users = json.loads((ROOT / "output/setup/local-users.json").read_text(encoding="utf-8-sig"))["users"]
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "User-authorized public-data test; no internal notes; no persistent config change",
        "models": [settings.model_research, settings.model_extraction, settings.model_synthesis],
        "fixture_mode": False,
        "test_retention_days": 1,
        "case_selection": args.case,
        "runs": [],
    }
    created, drafts = [], []
    headers = {"Origin": settings.app_base_url}

    def save():
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post("/v1/auth/login", json={k: users[0][k] for k in ("email", "password")})
        if login.status_code != 200:
            raise SystemExit("Local authentication failed.")
        try:
            for index in range(args.runs):
                case = CASES[index % 2 if args.case == "alternating" else int(args.case == "trumpf")]
                record = {"index": index + 1, "company": case["company"]["name"], "events": []}
                report["runs"].append(record)
                started = perf_counter()
                data = MeetingInput.model_validate(
                    {
                        **case,
                        "relationship": "new_contact",
                        "confirmed": True,
                        "public_identifiers_confirmed": True,
                        "internal_context": "",
                    }
                ).model_dump(mode="json")
                request_headers = {**headers, "Idempotency-Key": str(uuid4())}
                if index == 0 and case is CASES[0]:
                    text = "item Industrietechnik GmbH, https://www.item24.com; Heiner Giese und Thomas Neller; Setup-Meeting; Neukontakt."
                    parsed = client.post("/v1/input/parse", json={"text": text}, headers=headers)
                    record["parse_http"] = parsed.status_code
                    if parsed.status_code == 200:
                        draft = parsed.json()
                        drafts.append(draft["draft_id"])
                        (out / "free-text-preview.json").write_text(
                            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        preview = MeetingInput.model_validate(draft["preview"])
                        preview.confirmed = preview.public_identifiers_confirmed = True
                        data = preview.model_dump(mode="json")
                        request_headers["X-Draft-Id"] = draft["draft_id"]
                prepared = client.post("/v1/preparations", json=data, headers=request_headers)
                record["prepare_http"] = prepared.status_code
                if prepared.status_code != 202:
                    record.update(status="start_failed", response=prepared.json())
                    save()
                    continue
                ids = prepared.json()
                created.append(ids["meeting_id"])
                record.update(ids)
                workflow = Workflow(settings, app.state.repo, app.state.storage)
                workflow.provider = ObservedProvider(settings, record["events"])
                job = app.state.repo.claim_job("public-acceptance-" + stamp)
                if not job or job["run_id"] != ids["run_id"]:
                    raise RuntimeError("Queue is not isolated; refusing to process another meeting.")
                asyncio.run(workflow.process(job))
                run = client.get("/v1/runs/" + ids["run_id"]).json()
                record.update(
                    status=run["status"],
                    wall_seconds=round(perf_counter() - started, 3),
                    usage=run.get("usage"),
                    error=run.get("error"),
                )
                (out / f"run-{index + 1}.json").write_text(
                    json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if run.get("briefing_id"):
                    bid = run["briefing_id"]
                    result = client.get("/v1/briefings/" + bid).json()
                    (out / f"briefing-{index + 1}.json").write_text(
                        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    pdf = client.get("/v1/briefings/" + bid + "/pdf")
                    record.update(
                        pdf_http=pdf.status_code,
                        facts=len(result.get("facts", [])),
                        sources=len(result.get("sources", [])),
                        gaps=len(result.get("gaps", [])),
                        limits=result.get("limits", []),
                    )
                    if pdf.status_code == 200:
                        (out / f"briefing-{index + 1}.pdf").write_bytes(pdf.content)
                save()
                print(
                    json.dumps(
                        {k: v for k, v in record.items() if k not in ("events", "usage", "limits")},
                        ensure_ascii=True,
                    ),
                    flush=True,
                )
        finally:
            for mid in created:
                client.delete("/v1/meetings/" + mid, headers=headers)
            if app.state.repo:
                workflow = Workflow(settings, app.state.repo, app.state.storage)
                for _ in range(len(created) + 5):
                    if not workflow.purge_once():
                        break
                # Delete only drafts created by this test session.
                with app.state.repo._connection(worker=True) as db:
                    for draft_id in drafts:
                        db.execute("delete from atp.drafts where id=%s", (draft_id,))
            report["finished_at"] = datetime.now(UTC).isoformat()
            with app.state.repo._connection(worker=True) as db:
                report["test_meetings_deleted"] = all(
                    db.execute("select id from atp.meetings where id=%s", (mid,)).fetchone() is None
                    for mid in created
                )
                report["test_drafts_deleted"] = all(
                    db.execute("select id from atp.drafts where id=%s", (draft_id,)).fetchone() is None
                    for draft_id in drafts
                )
            save()
    print("REPORT=" + str(out), flush=True)


if __name__ == "__main__":
    main()
