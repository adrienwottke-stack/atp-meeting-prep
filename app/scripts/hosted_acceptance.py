"""Exercise the real HTTPS/web/worker/cloud/PDF chain with public test data."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://atp.127.0.0.1.sslip.io"
OUT = ROOT / "output/testberichte/hosted"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="Continue exactly the previously queued test")
    args = parser.parse_args()
    previous = json.loads((OUT / "report.json").read_text()) if args.resume else None
    account = json.loads((ROOT / "output/setup/cloud-admin-login.json").read_text(encoding="utf-8-sig"))
    assert account["project_ref"] == "edtudbpucezulbfrosum"
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(UTC).isoformat(), "base_url": BASE, "checks": []}
    meeting_id = None
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False, headers={"Origin": BASE}) as client:

        def check(value, label):
            assert value, label
            report["checks"].append(label)

        try:
            r = client.get("/health")
            check(
                r.status_code == 200 and r.json() == {"status": "ok", "fixture_mode": False},
                "https_health_real_mode",
            )
            check(client.get("/v1/meetings").status_code == 401, "unauthenticated_denied")
            r = client.post("/v1/auth/login", json={k: account[k] for k in ("email", "password")})
            check(r.status_code == 200, "administrator_login")
            cookie = next(c for c in client.cookies.jar if c.name == "atp_access")
            check(cookie.secure and cookie.has_nonstandard_attr("HttpOnly"), "secure_httponly_cookie")
            check(client.get("/v1/auth/me").json()["role"] == "admin", "administrator_role")
            check(
                client.post(
                    "/v1/preparations", json={}, headers={"Origin": "https://example.invalid"}
                ).status_code
                == 403,
                "cross_origin_write_denied",
            )
            payload = {
                "company": {"name": "item Industrietechnik GmbH", "website": "https://www.item24.com"},
                "people": [{"name": "Heiner Giese"}, {"name": "Thomas Neller"}],
                "meeting_type": "setup",
                "relationship": "new_contact",
                "confirmed": True,
                "public_identifiers_confirmed": True,
                "goal": "Technischer Hosted-Abnahmetest mit oeffentlichen Angaben",
                "internal_context": "",
            }
            started = time.monotonic()
            if previous:
                ids = {k: previous[k] for k in ("meeting_id", "run_id")}
                check(True, "previous_public_test_resumed")
            else:
                r = client.post("/v1/preparations", json=payload, headers={"Idempotency-Key": str(uuid4())})
                check(r.status_code == 202, "preparation_queued")
                ids = r.json()
            meeting_id = ids["meeting_id"]
            report.update(ids)
            while time.monotonic() - started < 360:
                r = client.get("/v1/runs/" + ids["run_id"])
                if r.status_code == 401:
                    login = client.post("/v1/auth/login", json={k: account[k] for k in ("email", "password")})
                    check(login.status_code == 200, "session_reauthentication")
                    r = client.get("/v1/runs/" + ids["run_id"])
                check(r.status_code == 200, "poll_authenticated")
                check(r.status_code == 200, "run_poll") if not report.get("poll_checked") else None
                report["poll_checked"] = True
                run = r.json()
                if run.get("briefing_id") or run["status"] in ("failed", "cancelled", "needs_clarification"):
                    break
                time.sleep(3)
            report.update(
                run_status=run["status"], seconds=round(time.monotonic() - started, 2), usage=run.get("usage")
            )
            check(run.get("briefing_id") is not None, "server_worker_generated_briefing")
            bid = run["briefing_id"]
            r = client.get(f"/v1/briefings/{bid}")
            check(r.status_code == 200, "briefing_read")
            (OUT / "briefing.json").write_text(
                json.dumps(r.json(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            r = client.get(f"/v1/briefings/{bid}/pdf")
            check(r.status_code == 200 and r.content.startswith(b"%PDF-"), "private_pdf_download")
            (OUT / "briefing.pdf").write_bytes(r.content)
            report["status"] = "passed"
        except Exception as exc:
            report.update(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, AssertionError):
                report["failed_check"] = str(exc)
        finally:
            if meeting_id:
                r = client.delete("/v1/meetings/" + meeting_id)
                report["cleanup_delete_http"] = r.status_code
            r = client.post("/v1/auth/logout")
            report["logout_http"] = r.status_code
            report["logged_out_denied"] = client.get("/v1/meetings").status_code == 401
            report["finished_at"] = datetime.now(UTC).isoformat()
            (OUT / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(
        json.dumps(
            {k: report.get(k) for k in ("status", "run_status", "seconds", "failed_check", "error_type")},
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
