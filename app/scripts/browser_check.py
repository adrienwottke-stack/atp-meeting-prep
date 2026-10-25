"""Authorized local Chromium smoke test. Web and worker must already be running."""

import argparse
import json
import re
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "testberichte"
BASE = "http://127.0.0.1:8000"


def finish_browser(context, browser, settings, cloud, cases, errors, requests, created, progress):
    cleanup_errors = []
    if cloud:
        for meeting_id in sorted(created):
            try:
                response = context.request.delete(
                    BASE + "/v1/meetings/" + meeting_id, headers={"Origin": BASE}
                )
                if response.status not in (202, 404):
                    cleanup_errors.append({"meeting_id": meeting_id, "status": response.status})
            except Exception as exc:
                cleanup_errors.append({"meeting_id": meeting_id, "error_type": type(exc).__name__})
    report = {
        "status": "passed"
        if progress["completed"] and not (errors or requests or cleanup_errors)
        else "failed",
        "fixture_mode": True,
        "browser": "Playwright Chromium",
        "cases": cases,
        "page_console_errors": errors,
        "failed_requests": requests,
        "supabase_url": settings.supabase_url,
        "storage_backend": settings.effective_storage_backend,
        "created_test_meeting_ids": sorted(created),
        "cleanup_errors": cleanup_errors,
        "note": "Synthetische Recherche; echte Supabase Auth/DB/privater Storage bei --cloud. Kein OpenAI-Recherche-Nachweis.",
    }
    (OUT / "browser-pruefung.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    browser.close()


def main(cloud=False):
    global OUT
    from atp.config import Settings

    settings = Settings()
    if settings.app_mode != "local" or not settings.fixture_mode or settings.app_base_url != BASE:
        raise SystemExit("Nur ausdrücklich lokaler synthetischer Fixture-Stack zulässig.")
    if cloud:
        manifest = json.loads((ROOT / "output/setup/cloud-project.json").read_text(encoding="utf-8"))
        account = json.loads((ROOT / "output/setup/cloud-admin-login.json").read_text(encoding="utf-8"))
        if (
            manifest["organization_id"] != "dozdfhvhttzctdekhklg"
            or account["project_ref"] != manifest["ref"]
            or settings.supabase_url != "https://" + manifest["ref"] + ".supabase.co"
            or settings.effective_storage_backend != "supabase"
        ):
            raise SystemExit("Cloud-Ziel stimmt nicht mit der aktiven ATP-Konfiguration überein.")
        users = [account]
        OUT = OUT / "cloud-browser"
    else:
        if settings.supabase_url != "http://127.0.0.1:55431":
            raise SystemExit("Für das ausdrücklich konfigurierte Cloudprojekt --cloud verwenden.")
        users = json.loads((ROOT / "output" / "setup" / "local-users.json").read_text(encoding="utf-8"))[
            "users"
        ]
    OUT.mkdir(parents=True, exist_ok=True)
    errors, failed_requests, cases = [], [], []
    created_meetings = set()
    progress = {"completed": False}
    with sync_playwright() as pw, ExitStack() as cleanup:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 960},
            accept_downloads=True,
            locale="de-DE",
            timezone_id="Europe/Berlin",
        )
        cleanup.callback(
            finish_browser,
            context,
            browser,
            settings,
            cloud,
            cases,
            errors,
            failed_requests,
            created_meetings,
            progress,
        )
        page = context.new_page()

        def remember_started_meeting(response):
            if (
                response.url == BASE + "/v1/preparations"
                and response.request.method == "POST"
                and response.status == 202
            ):
                created_meetings.add(response.json()["meeting_id"])

        page.on("response", remember_started_meeting)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on(
            "requestfailed",
            lambda request: failed_requests.append({"url": request.url, "error": request.failure}),
        )
        page.goto(BASE + "/login")
        page.locator("input[name=email]").fill(users[0]["email"])
        page.locator("input[name=password]").fill(users[0]["password"])
        page.locator("form").get_by_role("button", name="Anmelden", exact=True).click()
        expect(page).to_have_url(BASE + "/history")

        def open_optional(name):
            block = page.locator("details.optional-section").filter(
                has=page.locator("summary").filter(has_text=name)
            )
            if block.get_attribute("open") is None:
                block.locator("summary").click()

        def confirm_start(name="Briefing vorbereiten"):
            page.get_by_role("button", name="Angaben prüfen →", exact=True).click()
            expect(page.get_by_role("heading", name="Einmal prüfen. Dann recherchieren.")).to_be_visible()
            start = page.get_by_role("button", name=name, exact=True)
            expect(start).to_be_disabled()
            page.get_by_role("checkbox", name=re.compile("Ich habe die Angaben geprüft")).check()
            start.click()
            page.get_by_role("link", name="Briefing öffnen →", exact=True).click(timeout=60000)
            expect(page).to_have_url(re.compile(r"/briefings/[^/]+$"), timeout=60000)
            expect(page.get_by_role("link", name="PDF herunterladen ↓")).to_be_visible()
            expect(page.locator("#A04 h2")).to_contain_text("Fragen für das Gespräch")
            record = page.request.get(BASE + "/v1/briefings/" + page.url.rsplit("/", 1)[1]).json()
            created_meetings.add(record["meeting_id"])
            return page.url

        page.goto(BASE + "/")
        expect(page.locator("#environment-banner")).to_contain_text("Testmodus")
        page.locator("input[name=company_name]").fill("Musterwerk GmbH")
        page.locator("input[name=company_website]").fill("https://musterwerk.example")
        page.locator(".person-editor").nth(0).get_by_label("Name", exact=True).fill("Lea Sommer")
        page.get_by_role("button", name="+ Weitere Person", exact=True).click()
        page.locator(".person-editor").nth(1).get_by_label("Name", exact=True).fill("Tobias Kern")
        page.locator("select[name=meeting_type]").select_option("setup")
        open_optional("Gesprächsdetails")
        page.locator("select[name=relationship]").select_option("new_contact")
        open_optional("Weitere Informationen")
        page.locator("textarea[name=internal_context]").fill("TEST-INTERN-4711")
        page.screenshot(path=OUT / "browser-desktop-eingabe.png", full_page=True)
        clock = perf_counter()
        first_url = confirm_start()
        cases.append(
            {
                "case": "Formular mit zwei Personen",
                "url": first_url,
                "wall_seconds": round(perf_counter() - clock, 3),
                "status": "bestanden",
            }
        )
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=OUT / "browser-desktop-briefing.png", full_page=True)
        with page.expect_download() as info:
            page.get_by_role("link", name="PDF herunterladen ↓").click()
        download = info.value
        download.save_as(OUT / "browser-heruntergeladenes-briefing.pdf")
        assert (OUT / "browser-heruntergeladenes-briefing.pdf").read_bytes().startswith(b"%PDF-")
        cases.append({"case": "Geschützter PDF-Download aus Browser", "status": "bestanden"})

        page.locator("select[name=decision]").select_option("Ausnahme prüfen")
        page.locator("textarea[name=override_reason]").fill(
            "Kontrollierter Browsertest einer menschlichen Abweichung."
        )
        page.get_by_role("button", name="Entscheidung speichern", exact=True).click()
        expect(page.get_by_text(re.compile("Gespeicherte Entscheidung: Ausnahme prüfen"))).to_be_visible()
        cases.append({"case": "Menschliche Entscheidung mit Begründung", "status": "bestanden"})
        page.get_by_role("button", name="Neu recherchieren", exact=True).click()
        expect(page.locator(".person-editor")).to_have_count(2)
        page.locator("select[name=relationship]").select_option("existing_customer")
        expect(page.locator("select[name=relationship_context]")).to_be_visible()
        page.locator("select[name=relationship_context]").select_option("existing_obligation")
        page.locator("textarea[name=goal]").fill("Bestehende Zusammenarbeit und nächste Schritte klären.")
        second_url = confirm_start("Neue Version recherchieren")
        assert second_url != first_url
        expect(page.locator("main")).to_contain_text("Bestehende Verpflichtung")
        cases.append({"case": "Neurecherche/Version2, Bestandskunde/Verpflichtung", "status": "bestanden"})

        page.set_viewport_size({"width": 375, "height": 812})
        page.locator("#notification").wait_for(state="hidden", timeout=10000)
        page.evaluate("window.scrollTo(0,0)")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            "Mobiler horizontaler Overflow"
        )
        page.screenshot(path=OUT / "browser-mobile-briefing.png", full_page=True)
        expect(page.get_by_role("link", name="Neue Vorbereitung", exact=True)).to_be_visible()
        cases.append({"case": "Mobil 375x812, Navigation/Quellen/kein Overflow", "status": "bestanden"})

        page.set_viewport_size({"width": 1440, "height": 960})
        page.goto(BASE + "/")
        page.get_by_role("tab", name="Freitext", exact=True).click()
        page.locator("textarea[name=freetext]").fill(
            "Musterwerk GmbH, https://musterwerk.example; Lea Sommer Geschäftsführerin und Tobias Kern Leiter IT; Setup; Neukontakt"
        )
        with page.expect_response(
            lambda response: response.url.endswith("/v1/input/parse") and response.request.method == "POST",
            timeout=60000,
        ) as parsed_response:
            page.get_by_role("button", name="Vorschau aus Freitext erstellen", exact=True).click()
        assert parsed_response.value.status == 200, "Freitextextraktion im Browser fehlgeschlagen."
        expect(page.locator("input[name=company_name]")).to_have_value("Musterwerk GmbH")
        expect(page.locator(".person-editor")).to_have_count(2)
        text_url = confirm_start()
        cases.append(
            {
                "case": "Freitext, bearbeitbare Vorschau, Bestätigung, Briefing",
                "url": text_url,
                "status": "bestanden",
            }
        )
        page.goto(BASE + "/history")
        expect(page.get_by_role("heading", name="Musterwerk GmbH", exact=True).first).to_be_visible()
        page.locator("input[name=q]").fill("Musterwerk")
        page.get_by_role("button", name="Suchen", exact=True).click()
        expect(page.locator(".history-card").first).to_be_visible()
        page.screenshot(path=OUT / "browser-desktop-historie.png", full_page=True)
        cases.append({"case": "Historie und Firmensuche", "status": "bestanden"})
        page.goto(BASE + "/")
        long_name = (
            "Nordlicht für industrielle Präzisionssysteme und internationale Entwicklung " * 4 + "GmbH"
        )
        page.locator("input[name=company_name]").fill(long_name)
        page.locator("input[name=company_website]").fill("https://musterwerk.example")
        page.locator(".person-editor").nth(0).get_by_label("Name", exact=True).fill("Lea Sommer")
        page.get_by_role("button", name="+ Weitere Person", exact=True).click()
        page.locator(".person-editor").nth(1).get_by_label("Name", exact=True).fill("Tobias Kern")
        page.locator("select[name=meeting_type]").select_option("setup")
        open_optional("Gesprächsdetails")
        page.locator("select[name=relationship]").select_option("new_contact")
        page.get_by_role("button", name="Angaben prüfen →", exact=True).click()
        page.set_viewport_size({"width": 375, "height": 812})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=OUT / "browser-mobile-langer-name-vorschau.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 960})
        page.get_by_role("checkbox", name=re.compile("Ich habe die Angaben geprüft")).check()
        page.get_by_role("button", name="Briefing vorbereiten", exact=True).click()
        page.get_by_role("button", name="Eingabe korrigieren", exact=True).wait_for(timeout=60000)
        page.screenshot(path=OUT / "browser-rueckfrage.png", full_page=True)
        page.get_by_role("button", name="Eingabe korrigieren", exact=True).click()
        expect(page.locator("input[name=company_name]")).to_have_value(long_name)
        expect(page.locator(".person-editor")).to_have_count(2)
        page.locator("input[name=company_name]").fill("Musterwerk GmbH")
        last_url = confirm_start("Neue Version recherchieren")
        cases.append(
            {
                "case": "Langer Name/Mobil, sichtbare K02-Rückfrage, erhaltene Korrektur als neue Version",
                "status": "bestanden",
            }
        )
        record = page.request.get(BASE + "/v1/briefings/" + last_url.rsplit("/", 1)[1]).json()
        page.goto(BASE + "/history")
        card = page.locator(".history-card").filter(
            has=page.locator('a[href="/briefings/' + record["briefing_id"] + '"]')
        )
        card.get_by_role("button", name="Löschen", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Vorbereitung löschen", exact=True).click()
        expect(card).to_have_count(0)
        assert page.request.get(BASE + "/v1/briefings/" + record["briefing_id"] + "/pdf").status == 404
        cases.append({"case": "Löschung im Browser und sofort gesperrter PDF-Zugriff", "status": "bestanden"})
        page.goto(BASE + "/")
        page.set_viewport_size({"width": 375, "height": 812})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            "Formularoverflow mobil"
        )
        page.screenshot(path=OUT / "browser-mobile-eingabe.png", full_page=True)
        page.set_viewport_size({"width": 1440, "height": 960})
        progress["completed"] = True
    report = json.loads((OUT / "browser-pruefung.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed", "Browserprüfung oder gezielte Testdatenbereinigung fehlgeschlagen."
    print(
        f"Browserprüfung bestanden: {len(cases)} Abläufe, Desktop und Mobil, keine Console-/Netzwerkfehler."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", action="store_true")
    try:
        main(parser.parse_args().cloud)
    except Exception as exc:
        print(
            "Browserprüfung fehlgeschlagen: " + type(exc).__name__ + "; Details im ignorierten Testbericht."
        )
        raise SystemExit(1) from None
