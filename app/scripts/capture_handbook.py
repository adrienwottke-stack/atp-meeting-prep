"""Capture actual ATP UI with intercepted synthetic data; no live API calls.

From app: .venv/Scripts/python.exe scripts/capture_handbook.py
Requires dev dependencies and repository presentation fixtures.
"""

import json
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_clarification_presentation import mount  # noqa: E402
from test_pdf import sample_result  # noqa: E402

from atp.briefing_content import presentation_overview  # noqa: E402

ASSETS = ROOT / "src/atp/static/handbook"
OUT = ROOT / "output/handbook-visual"


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    pins, checks = [], []

    def capture(page, name, scroll=None, markers=()):
        page.evaluate("document.fonts.ready")
        if scroll:
            top = page.locator(scroll).first.evaluate("e => e.getBoundingClientRect().top + scrollY")
            page.evaluate("y => window.scrollTo(0, y)", max(0, top - 24))
        page.screenshot(path=str(OUT / (name + ".png")))
        with Image.open(OUT / (name + ".png")) as image:
            image.save(ASSETS / (name + ".webp"), "WEBP", lossless=True)
        for key, locator in markers:
            box = locator.bounding_box()
            assert box and 0 <= box["y"] < 760, (name, key, box)
            x, y = max(18, box["x"] - 13), box["y"] + min(box["height"] / 2, 25)
            pins.append(f".pin-{key} {{ left: {x / 1120 * 100:.3f}%; top: {y / 760 * 100:.3f}%; }}")
        checks.append(name)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        def view(payload, path, **options):
            page, _, errors, failed = mount(browser, payload, path, 1120, **options)
            page.set_viewport_size({"width": 1120, "height": 760})
            return page, errors, failed

        def close(page, errors, failed):
            assert not errors and not failed, (errors, failed)
            page.close()

        page, errors, failed = view({}, "/")
        page.get_by_label("Firmenname", exact=True).fill("Musterwerk GmbH")
        page.get_by_label("Website", exact=True).fill("https://musterwerk.example")
        page.get_by_label("Name", exact=True).fill("Lea Sommer")
        page.get_by_label("Meeting-Anlass", exact=True).select_option("setup")
        meeting_details = page.locator("details.optional-section").filter(
            has=page.locator("summary").filter(has_text="Gesprächsdetails")
        )
        meeting_details.locator("summary").click()
        page.get_by_label("Gesprächsziel", exact=False).fill("Prozesse und nächste Schritte klären")
        meeting_details.locator("summary").click()
        capture(
            page,
            "eingabe",
            ".tabs",
            [
                ("input-company", page.get_by_label("Firmenname", exact=True)),
                ("input-person", page.get_by_label("Name", exact=True)),
            ],
        )
        page.get_by_role("button", name="Angaben prüfen", exact=False).click()
        page.get_by_role("heading", name="Einmal prüfen. Dann recherchieren.").wait_for()
        page.get_by_role("checkbox").check()
        capture(
            page,
            "vorschau",
            ".surface",
            [
                ("preview-data", page.locator(".preview-row").first),
                ("preview-check", page.get_by_role("checkbox")),
                ("preview-start", page.get_by_role("button", name="Briefing vorbereiten", exact=True)),
            ],
        )
        close(page, errors, failed)
        payload = {
            "status": "needs_clarification",
            "version": 1,
            "clarifications": [
                {
                    "question_id": "goal",
                    "code": "K04",
                    "field": "goal",
                    "question": "Was soll nach dem Gespräch geklärt sein?",
                    "reason": "Das Ziel bestimmt die Empfehlung und die passenden Gesprächsfragen.",
                    "affected_sections": ["Empfehlung", "Gesprächsfragen"],
                    "options": [],
                    "answer_types": ["provide", "leave_unknown"],
                    "allow_unknown": True,
                }
            ],
        }
        page, errors, failed = view(payload, "/runs/beispiel")
        page.locator("#answer-goal-provide").fill("Die drei wichtigsten Ansatzpunkte für KI festhalten.")
        capture(
            page,
            "rueckfragen",
            ".clarification",
            [
                ("question", page.locator(".clarification h3")),
                ("answer", page.locator("#answer-goal-provide")),
                (
                    "continue",
                    page.get_by_role("button", name="Übernehmen und fortsetzen"),
                ),
            ],
        )
        close(page, errors, failed)
        sample = sample_result()
        sample.input.company.name = "Musterwerk GmbH"
        sample.company = sample.company[:1]
        sample.company[0].text = "Musterwerk fertigt Baugruppen."
        sample.sources[0].excerpt = "Musterwerk fertigt Baugruppen. 120 Mitarbeitende."
        sample.overview = presentation_overview(sample)
        result = sample.model_dump(mode="json")
        result["quality"] = {
            "state": "limited",
            "reasons": ["Aktuelle Rolle der Ansprechpartner noch offen."],
        }
        result["pdf_status"] = "completed"
        page, errors, failed = view(result, "/briefings/beispiel")
        page.locator(".overview-card").first.wait_for()
        capture(
            page,
            "briefing",
            ".briefing-intro",
            [("overview", page.locator(".overview-card").first.locator("h2"))],
        )
        page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
        capture(page, "qualitaet", ".quality-panel")
        page.get_by_text("Entscheidung festhalten", exact=True).click()
        capture(
            page,
            "entscheidung",
            "#decision",
            [
                ("decision", page.get_by_label("Deine Entscheidung", exact=True)),
                ("decision-save", page.get_by_role("button", name="Entscheidung speichern")),
            ],
        )
        close(page, errors, failed)
        items = {
            "items": [
                {
                    "meeting_id": "example",
                    "company_name": "Musterwerk GmbH",
                    "scheduled_at": "2026-10-20T08:00:00Z",
                    "status": "completed",
                    "latest_run_id": "v2",
                    "runs": [
                        {
                            "run_id": "v2",
                            "briefing_id": "example-2",
                            "status": "completed",
                            "created_at": "2026-10-15T13:00:00Z",
                        },
                        {
                            "run_id": "v1",
                            "briefing_id": "example-1",
                            "status": "completed",
                            "created_at": "2026-10-14T13:00:00Z",
                        },
                    ],
                }
            ]
        }
        page, errors, failed = view({}, "/history", responses={"/v1/meetings": items})
        page.get_by_text("2 Versionen anzeigen").click()
        capture(
            page,
            "historie",
            markers=[
                ("search", page.get_by_role("searchbox")),
                ("versions", page.locator(".history-card summary")),
            ],
        )
        close(page, errors, failed)
        context = {
            "id": "example",
            "name": "Musterwerk · KI-Pilot",
            "kind": "project",
            "description": "Ziele, Entscheidungen und offene Punkte zum gemeinsamen Pilotprojekt.",
            "owned": True,
            "editable": True,
            "expires_at": "2027-01-01T00:00:00Z",
        }
        responses = {
            "/v1/contexts": {"items": [context]},
            "/v1/memory/preview": {"entries": []},
            "/v1/memory/tasks": {"items": []},
        }
        for path, name, heading in [
            ("/knowledge", "wissen", "Was beim nächsten Gespräch zählt."),
        ]:
            page, errors, failed = view({}, path, memory_enabled=True, responses=responses)
            page.get_by_role("heading", name=heading).wait_for()
            capture(page, name)
            close(page, errors, failed)
        browser.close()
    (ASSETS / "pins.css").write_text(
        "/* Generated by scripts/capture_handbook.py. */\n" + "\n".join(pins) + "\n", "utf-8"
    )
    (ASSETS / "manifest.json").write_text(
        json.dumps(
            {
                "source": "Actual ATP UI rendered in Chromium; all API responses intercepted with synthetic data.",
                "viewport": {"width": 1120, "height": 760},
                "screenshots": checks,
                "live_api_calls": 0,
            },
            indent=2,
        ),
        "utf-8",
    )
    print(f"Captured {len(checks)} synthetic UI screenshots; no live API calls.")


if __name__ == "__main__":
    main()
