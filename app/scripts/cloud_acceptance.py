"""Explicit synthetic acceptance against the pinned ATP cloud project only.

No network traffic without --run. Never imports local users or local application data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from urllib.parse import unquote
from uuid import uuid4

import httpx
import psycopg
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from atp.config import APP_DIR, Settings
from atp.profiles import load_profile, profile_hash
from atp.repository import _clean
from atp.web import create_app
from atp.workflow import Workflow

MANIFEST = APP_DIR / "output/setup/cloud-project.json"
REPORT = APP_DIR / "output/testberichte/cloud-abnahme.json"


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def pinned_configuration():
    """Validate every connection before any network operation, including admin SQL."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    ref = manifest.get("ref") or manifest.get("project_ref") or manifest.get("id")
    require(isinstance(ref, str) and re.fullmatch(r"[a-z]{20}", ref), "INVALID_PROJECT_MANIFEST")
    settings = Settings(_env_file=APP_DIR / ".env.cloud")
    require(settings.supabase_url.rstrip("/") == f"https://{ref}.supabase.co", "WRONG_AUTH_PROJECT")
    require(settings.supabase_jwt_issuer == f"https://{ref}.supabase.co/auth/v1", "WRONG_JWT_ISSUER")
    require(settings.effective_storage_backend == "supabase", "CLOUD_STORAGE_REQUIRED")
    require(settings.auth_verify_session, "SESSION_VERIFICATION_REQUIRED")
    require(settings.storage_api_key, "ADMIN_API_KEY_REQUIRED")
    require((APP_DIR / ".env.cloud").is_file(), "CLOUD_ENV_FILE_REQUIRED")
    require(manifest.get("name") == "ATP", "WRONG_PROJECT_NAME")
    require(manifest.get("organization_id") == "dozdfhvhttzctdekhklg", "WRONG_ORGANIZATION")
    for field in ("database_url", "worker_database_url"):
        dsn = getattr(settings, field)
        require(
            hashlib.sha256(dsn.encode()).hexdigest() == manifest.get(field + "_sha256"),
            "DATABASE_MANIFEST_MISMATCH",
        )
        parts = conninfo_to_dict(dsn)
        host, user = parts.get("host", ""), unquote(parts.get("user", ""))
        direct = host == f"db.{ref}.supabase.co"
        pooler = host.endswith(".pooler.supabase.com") and user.endswith("." + ref)
        require(direct or pooler, "WRONG_DATABASE_PROJECT")
        require(parts.get("sslmode") == "verify-full", "DATABASE_VERIFIED_TLS_REQUIRED")
        require(parts.get("dbname", "postgres") == "postgres", "WRONG_DATABASE_NAME")
    # Only the research provider and local HTTP harness are synthetic. All services remain cloud.
    settings = settings.model_copy(
        update={"app_mode": "local", "fixture_mode": True, "app_base_url": "http://testserver"}
    )
    require(not settings.setup_issues(), "INCOMPLETE_CLOUD_CONFIGURATION")
    return settings, ref


def admin_headers(settings):
    key = settings.storage_api_key
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    return headers


def claim_owned(repo, run_id, org_id):
    """Claim exactly this test run; never take another user's queued work."""
    with repo._connection(worker=True) as db:
        job = db.execute(
            "select * from atp.jobs where run_id=%s and org_id=%s and status='queued' for update",
            (run_id, org_id),
        ).fetchone()
        require(job, "OWN_JOB_NOT_QUEUED")
        run = db.execute("select * from atp.runs where id=%s and org_id=%s", (run_id, org_id)).fetchone()
        token, now = str(uuid4()), datetime.now(UTC)
        db.execute(
            "update atp.jobs set status='running',worker_id='cloud-acceptance',lease_token=%s,"
            "lease_until=now()+(%s*interval '1 second'),heartbeat_at=now(),usage_saved_at=now(),"
            "attempt=attempt+1,updated_at=now() where id=%s",
            (token, repo.settings.lease_seconds, job["id"]),
        )
        db.execute("update atp.runs set status='researching',updated_at=now() where id=%s", (run_id,))
        return _clean(
            {
                "id": job["id"],
                "run_id": run_id,
                "lease_token": token,
                "checkpoint": job["checkpoint"],
                "phase": run["phase"],
                "input": run["clarified_input"],
                "profile": run["profile"],
                "profile_hash": run["profile_hash"],
                "profile_version": run["profile_version"],
                "result_version": run["result_version"],
                "meeting_id": run["meeting_id"],
                "org_id": org_id,
                "usage": job["usage"],
                "answers": run["answers"],
                "clarifications": run["clarifications"],
                "attempt": job["attempt"] + 1,
                "claimed_at": now,
            }
        )


def purge_owned(repo, storage, meeting_id, org_id):
    """Exercise the deletion contract while restricting the worker claim to this test."""
    with repo._connection(worker=True) as db:
        row = db.execute(
            "select * from atp.deletion_jobs where meeting_id=%s and org_id=%s for update",
            (meeting_id, org_id),
        ).fetchone()
        require(row, "OWN_DELETION_NOT_QUEUED")
        db.execute(
            "update atp.deletion_jobs set status='running',attempt=attempt+1,"
            "lease_until=now()+interval '90 seconds' where meeting_id=%s",
            (meeting_id,),
        )
        db.execute(
            "insert into atp.deletion_tombstones(meeting_id,org_id,requested_at) "
            "values(%s,%s,%s) on conflict do nothing",
            (meeting_id, org_id, row["created_at"]),
        )
    with repo.storage_lock(meeting_id):
        storage.delete_meeting(org_id, meeting_id)
        repo.finish_deletion(meeting_id)


def execute(settings, ref):
    report = {
        "project_ref": ref,
        "started_at": datetime.now(UTC).isoformat(),
        "research": "synthetic fixtures",
        "services": "real cloud Auth/Postgres/private Storage",
        "status": "failed",
        "checks": [],
        "remaining": {},
        "cleanup_errors": [],
    }
    org_id, users, meeting_id, storage_path = str(uuid4()), [], None, None
    report["test_org_id"] = org_id
    app = create_app(settings)

    def sql():
        return psycopg.connect(settings.worker_database_url, row_factory=dict_row, connect_timeout=10)

    def check(condition, code):
        report["last_check"] = code
        require(condition, code)
        report["checks"].append(code)

    with httpx.Client(
        base_url=settings.supabase_url,
        headers=admin_headers(settings),
        trust_env=False,
        follow_redirects=False,
        timeout=30,
    ) as admin:
        try:
            # Admin create_user confirms these reserved-domain accounts without sending mail.
            for _ in range(2):
                user = {
                    "id": str(uuid4()),
                    "email": f"atp-cloud-{uuid4().hex}@example.invalid",
                    "password": secrets.token_urlsafe(36),
                }
                # Retain the caller-chosen ID even if the create response times out.
                users.append(user)
                report["remaining"]["user_ids"] = [u["id"] for u in users]
                response = admin.post(
                    "/auth/v1/admin/users",
                    json={
                        **user,
                        "email_confirm": True,
                        "user_metadata": {"synthetic_cloud_acceptance": True},
                    },
                )
                require(response.status_code in (200, 201), "TEST_USER_CREATE_FAILED")
                require(response.json()["id"] == user["id"], "TEST_USER_ID_MISMATCH")
            with sql() as db:
                for i, user in enumerate(users):
                    db.execute(
                        "insert into atp.memberships(user_id,org_id,role) values(%s,%s,%s)",
                        (user["id"], org_id, "admin" if i == 0 else "member"),
                    )
                profile = load_profile(settings.atp_profile_version)
                digest = profile_hash(profile)
                db.execute(
                    "insert into atp.profile_versions(org_id,version,content_hash,content,active,"
                    "activated_by) values(%s,%s,%s,%s,true,%s)",
                    (org_id, profile["profile_id"], digest, Jsonb(profile), users[0]["id"]),
                )
                db.execute(
                    "insert into atp.profile_activations(org_id,version,content_hash,activated_by,"
                    "reason) values(%s,%s,%s,%s,%s)",
                    (
                        org_id,
                        profile["profile_id"],
                        digest,
                        users[0]["id"],
                        "SYNTHETISCHE Cloud-Abnahme: nur neu erzeugte Test-Org; keine ATP-Freigabe.",
                    ),
                )
            report["remaining"]["org_id"] = org_id
            with (
                TestClient(app, raise_server_exceptions=False) as owner,
                TestClient(app, raise_server_exceptions=False) as other,
            ):
                for client, user in zip((owner, other), users, strict=True):
                    response = client.post("/v1/auth/login", json={k: user[k] for k in ("email", "password")})
                    check(response.status_code == 200, "cloud_auth_login_" + user["id"])
                    client.headers["Origin"] = settings.app_base_url
                payload = {
                    "company": {"name": "Musterwerk GmbH", "website": "https://musterwerk.example"},
                    "people": [
                        {"name": "Lea Sommer", "role_hint": "Geschäftsführerin"},
                        {"name": "Tobias Kern", "role_hint": "Leiter IT"},
                    ],
                    "meeting_type": "setup",
                    "relationship": "new_contact",
                    "confirmed": True,
                    "public_identifiers_confirmed": True,
                }
                response = owner.post(
                    "/v1/preparations", json=payload, headers={"Idempotency-Key": str(uuid4())}
                )
                check(response.status_code == 202, "cloud_preparation_created")
                started = response.json()
                meeting_id = started["meeting_id"]
                report["remaining"]["meeting_id"] = meeting_id
                job = claim_owned(app.state.repo, started["run_id"], org_id)
                asyncio.run(Workflow(settings, app.state.repo, app.state.storage).process(job))
                run = owner.get("/v1/runs/" + started["run_id"]).json()
                check(run.get("status") == "completed", "fixture_workflow_completed")
                bid = run["briefing_id"]
                result = owner.get("/v1/briefings/" + bid)
                check(result.status_code == 200 and result.json()["fixture_mode"], "stored_fixture_briefing")
                pdf = owner.get(f"/v1/briefings/{bid}/pdf")
                check(pdf.status_code == 200 and pdf.content.startswith(b"%PDF-"), "private_cloud_pdf_get")
                decision = owner.post(
                    f"/v1/meetings/{meeting_id}/decision",
                    json={
                        "run_id": started["run_id"],
                        "confirmed_decision": "Ausnahme prüfen",
                        "override_reason": "Synthetische Cloud-Abnahme einer menschlichen Entscheidung.",
                    },
                )
                check(decision.status_code == 200, "human_decision_persisted")
                storage_path = f"{org_id}/{meeting_id}/{started['run_id']}/result.pdf"
                report["remaining"]["storage_path"] = storage_path
                bucket = admin.get("/storage/v1/bucket/" + settings.storage_bucket)
                check(
                    bucket.status_code == 200 and bucket.json().get("public") is False,
                    "storage_bucket_private",
                )
                with httpx.Client(
                    base_url=settings.supabase_url, trust_env=False, follow_redirects=False, timeout=20
                ) as unprivileged:
                    object_route = (
                        f"/storage/v1/object/authenticated/{settings.storage_bucket}/{storage_path}"
                    )
                    anonymous = unprivileged.get(object_route, headers={"apikey": settings.supabase_anon_key})
                    check(anonymous.status_code in (400, 401, 403, 404), "anonymous_storage_read_denied")
                    outsider = unprivileged.get(
                        object_route,
                        headers={
                            "apikey": settings.supabase_anon_key,
                            "Authorization": "Bearer " + other.cookies.get("atp_access"),
                        },
                    )
                    check(
                        outsider.status_code in (400, 401, 403, 404), "second_user_direct_storage_read_denied"
                    )
                for route in (
                    f"/v1/runs/{started['run_id']}",
                    f"/v1/briefings/{bid}",
                    f"/v1/briefings/{bid}/pdf",
                ):
                    check(other.get(route).status_code == 404, "second_user_excluded_" + route)
                actor = asyncio.run(app.state.auth.authenticate(other.cookies.get("atp_access")))
                with app.state.repo._connection(actor) as db:
                    check(
                        not db.execute("select id from atp.meetings where id=%s", (meeting_id,)).fetchone(),
                        "direct_sql_rls_excludes_second_user",
                    )
                response = owner.delete("/v1/meetings/" + meeting_id)
                check(response.status_code == 202, "meeting_delete_accepted")
                check(owner.get(f"/v1/briefings/{bid}/pdf").status_code == 404, "pdf_immediately_revoked")
                purge_owned(app.state.repo, app.state.storage, meeting_id, org_id)
                with sql() as db:
                    check(
                        not db.execute("select id from atp.meetings where id=%s", (meeting_id,)).fetchone(),
                        "meeting_physically_purged",
                    )
                listing = admin.post(
                    "/storage/v1/object/list/" + settings.storage_bucket,
                    json={"prefix": f"{org_id}/{meeting_id}", "limit": 100},
                )
                check(listing.status_code == 200 and listing.json() == [], "storage_object_purged")
                for client in (owner, other):
                    token = client.cookies.get("atp_access")
                    check(client.post("/v1/auth/logout").status_code == 200, "logout_endpoint")
                    check(client.get("/v1/auth/me").status_code == 401, "logout_cookie_removed")
                    check(
                        client.get("/v1/auth/me", headers={"Cookie": "atp_access=" + token}).status_code
                        == 401,
                        "logged_out_jwt_session_rejected",
                    )
            report["status"] = "passed"
        except Exception as exc:
            # Never serialize exception messages, HTTP bodies, DSNs, tokens or passwords.
            report["failure_type"] = type(exc).__name__
            if type(exc) is RuntimeError and re.fullmatch(r"[A-Z_]+", str(exc)):
                report["failure_code"] = str(exc)
        finally:
            # Only this newly generated org/these newly created IDs are ever deleted.
            if app.state.storage is not None:
                try:
                    with sql() as db:
                        meetings = db.execute(
                            "select id from atp.meetings where org_id=%s", (org_id,)
                        ).fetchall()
                    for row in meetings:
                        app.state.storage.delete_meeting(org_id, str(row["id"]))
                except Exception as exc:
                    report["cleanup_errors"].append("storage:" + type(exc).__name__)
            try:
                with sql() as db:
                    for table in (
                        "meetings",
                        "drafts",
                        "storage_cleanup",
                        "deletion_tombstones",
                        "profile_activations",
                        "profile_versions",
                        "memberships",
                    ):
                        db.execute(f"delete from atp.{table} where org_id=%s", (org_id,))
                report["remaining"].pop("org_id", None)
                report["remaining"].pop("meeting_id", None)
            except Exception as exc:
                report["cleanup_errors"].append("database:" + type(exc).__name__)
            remaining_users = []
            for user in users:
                try:
                    response = admin.delete("/auth/v1/admin/users/" + user["id"])
                    require(response.status_code in (200, 204, 404), "TEST_USER_DELETE_FAILED")
                except Exception as exc:
                    remaining_users.append(user["id"])
                    report["cleanup_errors"].append("auth:" + type(exc).__name__)
            report["remaining"]["user_ids"] = remaining_users
            try:
                listing = admin.post(
                    "/storage/v1/object/list/" + settings.storage_bucket,
                    json={"prefix": org_id, "limit": 100},
                )
                require(listing.status_code == 200, "STORAGE_CLEANUP_VERIFY_FAILED")
                objects = listing.json()
                report["remaining"].pop("storage_path", None)
                if objects:
                    report["remaining"]["storage_paths"] = [org_id + "/" + row["name"] for row in objects]
                    report["remaining"]["org_id"] = org_id
            except Exception as exc:
                report["cleanup_errors"].append("storage_verify:" + type(exc).__name__)
            if report["cleanup_errors"] or remaining_users or report["remaining"].get("storage_paths"):
                report["status"] = "failed"
            report["finished_at"] = datetime.now(UTC).isoformat()
            REPORT.parent.mkdir(parents=True, exist_ok=True)
            REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Create and remove synthetic cloud test data")
    args = parser.parse_args()
    if not args.run:
        print("Keine Ausführung. Bewusster Start: python scripts/cloud_acceptance.py --run")
        return 0
    try:
        settings, ref = pinned_configuration()
        report = execute(settings, ref)
    except Exception:
        print(
            "Cloud-Abnahme vor Ausführung blockiert; Zielmanifest und .env.cloud prüfen. Keine Secrets ausgegeben."
        )
        return 1
    print(f"Cloud-Abnahme: {report['status']}; Bericht: {REPORT.relative_to(APP_DIR)}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
