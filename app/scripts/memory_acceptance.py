"""Server-container acceptance with real cloud Auth/Postgres/Storage and isolated synthetic jobs."""

import asyncio
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from atp.config import Settings
from atp.memory_workflow import MemoryWorkflow
from atp.profiles import load_profile, profile_hash
from atp.repository import Repository
from atp.storage import Storage
from atp.web import create_app
from atp.workflow import Workflow


async def main():

    base = Settings()
    queue = "memory-acceptance-" + uuid4().hex
    settings = base.model_copy(
        update={
            "app_mode": "local",
            "fixture_mode": True,
            "memory_enabled": True,
            "memory_user_ids": "",
            "worker_queue": queue,
            "retention_days": 90,
        }
    )
    repo = Repository(settings)
    storage = Storage(settings)
    worker = MemoryWorkflow(settings, repo, storage)
    org, foreign_org = str(uuid4()), str(uuid4())
    users = []
    contexts = []
    paths = []
    checks = []
    clients = []
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "environment": "server container / real Supabase / synthetic provider",
        "queue": queue,
        "checks": checks,
    }

    def check(ok, label):
        if not ok:
            raise AssertionError(label)
        checks.append(label)

    headers = {"apikey": settings.storage_api_key}
    if not settings.storage_api_key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + settings.storage_api_key
    async with httpx.AsyncClient(
        base_url=settings.supabase_url, headers=headers, timeout=30, trust_env=False
    ) as admin:
        try:
            for i in range(3):
                user = {
                    "id": str(uuid4()),
                    "email": f"atp-memory-{uuid4().hex}@example.invalid",
                    "password": secrets.token_urlsafe(32),
                }
                users.append(user)
                r = await admin.post("/auth/v1/admin/users", json={**user, "email_confirm": True})
                check(r.status_code in (200, 201), "test_user_created_" + str(i))
            with repo._connection(worker=True) as db:
                for i, user in enumerate(users):
                    db.execute(
                        "insert into atp.memberships(user_id,org_id,role) values(%s,%s,'admin')",
                        (user["id"], foreign_org if i == 2 else org),
                    )
                profile = load_profile()
                db.execute(
                    "insert into atp.profile_versions(org_id,version,content,content_hash,active,activated_by) values(%s,%s,%s,%s,true,%s)",
                    (
                        org,
                        profile["profile_id"],
                        __import__("psycopg").types.json.Jsonb(profile),
                        profile_hash(profile),
                        users[0]["id"],
                    ),
                )
            app = create_app(settings)
            for user in users:
                c = httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=settings.app_base_url,
                    headers={"Origin": settings.app_base_url},
                    timeout=40,
                )
                clients.append(c)
                r = await c.post("/v1/auth/login", json={k: user[k] for k in ("email", "password")})
                check(r.status_code == 200, "authenticated_cloud_session")
            a, b, foreign = clients

            async def post(path, data, expected=201, client=a, key=None):
                r = await client.post(path, json=data, headers={"Idempotency-Key": key or str(uuid4())})
                if r.status_code != expected:
                    raise AssertionError(f"{path}: {r.status_code} {r.text[:300]}")
                return r.json()

            async def context(kind, name):
                c = await post("/v1/contexts", {"kind": kind, "name": name})
                contexts.append(c["id"])
                return c["id"]

            p = await context("project", "Synthetisches Projekt A")
            other = await context("project", "Synthetisches Projekt B")
            general = await context("general", "Synthetische allgemeine Learnings")
            company = await context("company", "Synthetischer Unternehmenskontext")
            e = await post(
                f"/v1/contexts/{p}/entries",
                {
                    "text": "Das vorhandene System wird in diesem Projekt weiterverwendet.",
                    "kind": "decision",
                    "source": "Synthetischer Beschluss",
                    "status": "confirmed",
                },
            )
            await post(
                f"/v1/contexts/{p}/entries",
                {"text": "UNBESTÄTIGTES_GEHEIMNIS", "source": "Entwurf", "status": "draft"},
            )
            check((await b.get("/v1/contexts/" + p)).status_code == 404, "same_org_private_denied")
            check((await foreign.get("/v1/contexts/" + p)).status_code == 404, "foreign_org_denied")
            await post(f"/v1/contexts/{p}/shares", {"user_id": users[1]["id"], "permission": "read"}, 200)
            check((await b.get("/v1/contexts/" + p)).status_code == 200, "explicit_read_share")
            check(
                (await b.post(f"/v1/contexts/{p}/entries", json={"text": "unauthorized"})).status_code == 404,
                "read_share_cannot_edit",
            )
            check(
                (
                    await a.post(
                        f"/v1/contexts/{p}/shares", json={"user_id": users[2]["id"], "permission": "read"}
                    )
                ).status_code
                == 404,
                "cross_org_share_denied",
            )

            check(
                (await a.post("/v1/project-preparations", json={})).status_code == 404,
                "project_creation_removed",
            )
            snapshot = (await a.get("/v1/memory/preview", params={"context_ids": p})).json()
            check(e["id"] in [item["id"] for item in snapshot["entries"]], "confirmed_memory_used")
            check("UNBESTÄTIGTES_GEHEIMNIS" not in json.dumps(snapshot), "draft_not_used")

            async def learning(cid, key=None):
                feedback = await post(
                    "/v1/memory/feedback",
                    {"context_id": cid, "text": "Synthetisch: Zuerst den bestätigten Stand klären."},
                )
                return await post(
                    "/v1/memory/proposals",
                    {"feedback_id": feedback["id"], "target_context_id": cid},
                    202,
                    key=key,
                )

            proposal = await learning(p)
            job = worker.claim()
            check(job["id"] == proposal["id"] and job["kind"] == "learning", "only_learning_queue_claimed")
            await worker.process(job)
            result = (await a.get("/v1/memory/tasks/" + proposal["id"])).json()
            check(result["status"] == "completed", "learning_completed")
            check(result["result"]["usage"]["search_calls"] == 0, "learning_no_web_search")
            check(
                (await a.get("/v1/memory/tasks/" + proposal["id"] + "/pdf")).status_code == 404,
                "project_pdf_removed",
            )
            await a.delete(f"/v1/contexts/{p}/shares/{users[1]['id']}")
            check(
                (await b.get("/v1/memory/tasks/" + proposal["id"])).status_code == 404,
                "revocation_hides_learning",
            )
            learned = await post(
                f"/v1/memory/tasks/{proposal['id']}/confirm",
                {"text": result["result"]["narrative"]["text"]},
                200,
            )
            check(learned["status"] == "confirmed", "explicit_learning_confirmation")
            check(
                (await post(f"/v1/memory/tasks/{proposal['id']}/confirm", {"text": learned["text"]}, 200))[
                    "id"
                ]
                == learned["id"],
                "confirmation_idempotency",
            )
            # Restart/resume now exercises supported Learning work instead of removed project generation.
            resumed = await learning(other)
            old_job = worker.claim()
            narrative = worker.fixture(old_job).model_dump(mode="json")
            check(
                worker.save(old_job, usage={"model_requests": 1}, checkpoint={"narrative": narrative}),
                "resume_checkpoint_saved",
            )
            with repo.connection(worker=True) as db:
                db.execute(
                    "update atp.memory_tasks set lease_until=now()-interval '1 second' where id=%s",
                    (resumed["id"],),
                )
            recovered = MemoryWorkflow(settings, repo, storage).claim()
            check(recovered["lease_token"] != old_job["lease_token"], "restart_new_lease")
            check(not worker.save(old_job, result={"invalid": True}), "old_lease_cannot_publish")
            await worker.process(recovered)
            check(
                (await a.get("/v1/memory/tasks/" + resumed["id"])).json()["result"]["usage"]["model_requests"]
                == 1,
                "resume_without_duplicate_model_call",
            )
            # General lessons require explicit scope, confirmation and meeting-type match.
            g = await post(
                f"/v1/contexts/{general}/entries",
                {
                    "text": "Im Projektgespräch Verantwortliche und Termin klären.",
                    "kind": "learning",
                    "status": "confirmed",
                    "applies_to": ["project_review"],
                },
            )
            preview = (
                await a.get("/v1/memory/preview", params={"context_ids": other, "meeting_type": "setup"})
            ).json()
            check(not preview["entries"], "general_lesson_type_filtered")
            preview = (
                await a.get(
                    "/v1/memory/preview", params={"context_ids": other, "meeting_type": "project_review"}
                )
            ).json()
            check(g["id"] in [e["id"] for e in preview["entries"]], "general_lesson_in_scope")
            package = (await a.get("/v1/memory/export", params={"context_ids": p})).json()
            target = await context("project", "Synthetisches Importziel")
            imp = {"package": package, "mapping": {p: target}, "apply": False}
            check((await post("/v1/memory/import", imp, 200))["applied"] is False, "import_preview")
            await post("/v1/memory/import", {**imp, "apply": True}, 200)
            imported = await post("/v1/memory/import", {**imp, "apply": True}, 200)
            check(all(e["action"] == "already_imported" for e in imported["entries"]), "import_idempotency")
            check(
                (await a.get("/v1/contexts/" + target)).json()["entries"][0]["source"]
                in [e["source"] for e in package["contexts"][0]["entries"]],
                "import_provenance",
            )
            # Existing company pipeline keeps deterministic ICP and private memory separate.
            ce = await post(
                f"/v1/contexts/{company}/entries",
                {"text": "Im Gespräch den Schulungsbedarf ausdrücklich klären.", "status": "confirmed"},
            )
            payload = {
                "company": {"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
                "people": [{"name": "Lea Sommer"}, {"name": "Tobias Kern"}],
                "meeting_type": "setup",
                "relationship": "new_contact",
                "goal": "Synthetischer Firmenregressionstest",
                "confirmed": True,
                "public_identifiers_confirmed": True,
                "context_ids": [company],
            }
            preparation = await post("/v1/preparations", payload, 202)
            job = repo.claim_job("memory-acceptance")
            check(job["run_id"] == preparation["run_id"], "company_test_queue_claim")
            await Workflow(settings, repo, storage).process(job)
            run = (await a.get("/v1/runs/" + preparation["run_id"])).json()
            check(bool(run.get("briefing_id")), "company_regression_complete")
            brief = (await a.get("/v1/briefings/" + run["briefing_id"])).json()
            paths.append(f"{org}/{preparation['meeting_id']}/{preparation['run_id']}/result.pdf")
            check(
                brief["icp"]["status"] == "matching"
                and ce["id"] in [e["id"] for e in brief["memory"]["entries"]],
                "company_icp_unchanged_memory_attached",
            )
            with repo._connection(worker=True) as db:
                db.execute(
                    "insert into atp.shares(meeting_id,user_id,permission,granted_by) values(%s,%s,'read',%s)",
                    (preparation["meeting_id"], users[1]["id"], users[0]["id"]),
                )
            check(
                (await b.get("/v1/briefings/" + run["briefing_id"])).status_code == 404,
                "briefing_share_does_not_widen_context",
            )
            # Completed tasks and resume checkpoints both vanish on deletion.
            check(
                (await a.delete(f"/v1/contexts/{p}/entries/{e['id']}")).status_code == 202, "entry_deletion"
            )
            check(
                (await a.get("/v1/memory/tasks/" + proposal["id"])).status_code == 404,
                "derived_learning_deleted",
            )
            check(
                (await a.get("/v1/memory/preview", params={"context_ids": p})).status_code == 200,
                "remaining_context_usable",
            )
            report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["failure"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            if type(exc).__name__ == "InsufficientPrivilege":
                report["permission_error"] = exc.diag.message_primary
            import traceback

            report["error_location"] = [
                {"file": Path(t.filename).name, "line": t.lineno, "function": t.name}
                for t in traceback.extract_tb(exc.__traceback__)[-4:]
            ]
        finally:
            for path in paths:
                try:
                    storage.delete_pdf(path)
                except Exception:
                    pass
            with repo._connection(worker=True) as db:
                db.execute("delete from atp.meetings where org_id in (%s,%s)", (org, foreign_org))
                db.execute("delete from atp.memory_tasks where org_id in (%s,%s)", (org, foreign_org))
                db.execute("delete from atp.memory_links where context_id=any(%s::uuid[])", (contexts,))
                db.execute("delete from atp.memory_contexts where org_id in (%s,%s)", (org, foreign_org))
                db.execute("delete from atp.profile_versions where org_id in (%s,%s)", (org, foreign_org))
                db.execute("delete from atp.memberships where org_id in (%s,%s)", (org, foreign_org))
                db.execute("delete from atp.storage_cleanup where org_id in (%s,%s)", (org, foreign_org))
            for user in users:
                await admin.delete("/auth/v1/admin/users/" + user["id"])
            for client in clients:
                await client.aclose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
