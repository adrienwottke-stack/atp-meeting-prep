"""Real HTTPS/model acceptance and desktop/mobile browser proof. Uses only labelled synthetic data."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/memory-implementation"
BASE = "https://atp.127.0.0.1.sslip.io"


def main():

    OUT.mkdir(parents=True, exist_ok=True)
    account = json.loads((ROOT / "output/setup/cloud-admin-login.json").read_text(encoding="utf-8-sig"))
    if account["project_ref"] != "edtudbpucezulbfrosum":
        raise SystemExit("Wrong project")
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "checks": [],
        "status": "failed",
        "console_errors": [],
        "failed_requests": [],
    }
    contexts = []
    with httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=40, trust_env=False) as client:

        def check(ok, label):
            if not ok:
                raise AssertionError(label)
            report["checks"].append(label)

        def post(path, value, expected=201):
            r = client.post(path, json=value, headers={"Idempotency-Key": str(uuid4())})
            if r.status_code != expected:
                raise AssertionError(path + ": " + str(r.status_code))
            return r.json()

        def wait(task):
            started = time.monotonic()
            while time.monotonic() - started < 240:
                r = client.get("/v1/memory/tasks/" + task["id"])
                r.raise_for_status()
                value = r.json()
                if value["status"] in ("completed", "failed", "cancelled"):
                    check(value["status"] == "completed", "real_server_worker_" + value["kind"])
                    return value
                time.sleep(3)
            raise AssertionError("server_task_timeout")

        try:
            for _ in range(30):
                try:
                    health = client.get("/health")
                    if health.status_code == 200 and health.json().get("status") == "ok":
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(1)
            check(
                client.post("/v1/auth/login", json={k: account[k] for k in ("email", "password")}).status_code
                == 200,
                "real_https_login",
            )
            check(client.get("/v1/memory/status").json()["enabled"], "feature_enabled_for_test_user")
            c = post(
                "/v1/contexts",
                {
                    "name": "Abnahme · Projektwissen (synthetisch)",
                    "kind": "project",
                    "description": "Reiner technischer Test. Keine Kundendaten oder tatsächlichen Projektentscheidungen.",
                },
            )
            contexts.append(c["id"])
            post(
                f"/v1/contexts/{c['id']}/entries",
                {
                    "kind": "decision",
                    "text": "Für dieses synthetische Projekt ist entschieden, das vorhandene System weiterzuverwenden. Ein Systemwechsel ist nicht beauftragt.",
                    "source": "Synthetischer Abnahmebeschluss",
                    "information_as_of": "2026-10-15",
                    "status": "confirmed",
                },
            )
            check(
                client.post("/v1/project-preparations", json={}).status_code == 404,
                "project_creation_removed",
            )
            f = post(
                "/v1/memory/feedback",
                {
                    "context_id": c["id"],
                    "kind": "outcome",
                    "text": "Im synthetischen Gespräch wurde bestätigt: Vor technischen Änderungen müssen das konkrete Ziel, die verantwortliche Person und ein prüfbarer nächster Schritt geklärt werden.",
                },
            )
            proposal = wait(
                post("/v1/memory/proposals", {"feedback_id": f["id"], "target_context_id": c["id"]}, 202)
            )
            check(bool(proposal["result"]["narrative"]["text"]), "real_learning_proposed")
            learned = post(
                f"/v1/memory/tasks/{proposal['id']}/confirm",
                {"text": proposal["result"]["narrative"]["text"], "kind": "learning", "status": "confirmed"},
                200,
            )
            preview = client.get("/v1/memory/preview", params={"context_ids": c["id"]}).json()
            check(learned["id"] in [e["id"] for e in preview["entries"]], "confirmed_learning_available")
            check(
                proposal["result"]["usage"]["model_requests"] >= 1
                and proposal["result"]["usage"]["search_calls"] == 0,
                "real_learning_model_without_web_tools",
            )
            report["model"] = proposal["result"].get("model")
            report["learning"] = learned["text"]
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                bc = browser.new_context(
                    viewport={"width": 1440, "height": 1000}, locale="de-DE", timezone_id="Europe/Berlin"
                )
                page = bc.new_page()
                page.on("pageerror", lambda error: report["console_errors"].append(str(error)))
                page.on(
                    "console",
                    lambda msg: report["console_errors"].append(msg.text) if msg.type == "error" else None,
                )
                page.on(
                    "response",
                    lambda response: (
                        report["failed_requests"].append(
                            {"path": response.url.split(BASE)[-1], "status": response.status}
                        )
                        if response.status >= 400 and response.url.startswith(BASE)
                        else None
                    ),
                )
                page.goto(BASE + "/login")
                page.get_by_label("E-Mail-Adresse").fill(account["email"])
                page.get_by_label("Passwort", exact=True).fill(account["password"])
                page.locator("#main").get_by_role("button", name="Anmelden", exact=True).click()
                page.wait_for_url(BASE + "/history")
                page.get_by_role("link", name="Wissen & Learnings", exact=True).click()
                expect(page.get_by_role("heading", name="Was beim nächsten Gespräch zählt.")).to_be_visible()
                page.screenshot(path=str(OUT / "knowledge-desktop.png"), full_page=True)
                page.goto(BASE + "/knowledge/" + c["id"])
                expect(page.get_by_role("heading", name=c["name"], exact=True)).to_be_visible()
                expect(page.get_by_text(learned["text"], exact=True).first).to_be_visible()
                # Exercise the new UI mutation with an unconfirmed, harmless draft.
                form = page.locator(".memory-grid > section").nth(1)
                form.get_by_label("Erkenntnis", exact=True).fill(
                    "Synthetischer Browserentwurf, nicht zur Wiederverwendung bestätigt."
                )
                form.get_by_role("button", name="Erkenntnis speichern", exact=True).click()
                expect(
                    page.get_by_text(
                        "Synthetischer Browserentwurf, nicht zur Wiederverwendung bestätigt.", exact=True
                    )
                ).to_be_visible()
                page.screenshot(path=str(OUT / "context-desktop.png"), full_page=True)
                page.goto(BASE + "/memory-tasks/" + proposal["id"])
                expect(page.get_by_role("heading", name="Vorschlag prüfen", exact=True)).to_be_visible()
                page.screenshot(path=str(OUT / "learning-desktop.png"), full_page=True)
                check(
                    page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
                    "desktop_no_horizontal_overflow",
                )
                page.set_viewport_size({"width": 375, "height": 812})
                page.screenshot(path=str(OUT / "learning-mobile.png"), full_page=True)
                check(
                    page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
                    "mobile_learning_no_horizontal_overflow",
                )
                page.goto(BASE + "/knowledge/" + c["id"])
                expect(page.get_by_role("heading", name=c["name"], exact=True)).to_be_visible()
                page.screenshot(path=str(OUT / "context-mobile.png"), full_page=True)
                check(
                    page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
                    "mobile_context_no_horizontal_overflow",
                )
                page.get_by_role("link", name="← Zurück", exact=True).click()
                expect(page.get_by_role("heading", name="Learning-Vorschläge", exact=True)).to_be_visible()
                check(
                    page.get_by_role("link", name="Projektbriefing", exact=True).count() == 0,
                    "project_tab_removed",
                )
                page.screenshot(path=str(OUT / "learning-history-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1000})
                check(not report["console_errors"], "no_browser_javascript_errors")
                check(not report["failed_requests"], "no_failed_browser_requests")
                browser.close()
            report["status"] = "passed"
        except Exception as exc:
            report["failure"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            raise
        finally:
            report["cleanup"] = []
            for cid in contexts:
                try:
                    report["cleanup"].append(
                        {"context_id": cid, "status": client.delete("/v1/contexts/" + cid).status_code}
                    )
                except Exception as exc:
                    report["cleanup"].append({"error_type": type(exc).__name__})
            (OUT / "hosted-acceptance.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "checks": len(report["checks"]),
                        "report": "output/memory-implementation/hosted-acceptance.json",
                    }
                )
            )


if __name__ == "__main__":
    main()
