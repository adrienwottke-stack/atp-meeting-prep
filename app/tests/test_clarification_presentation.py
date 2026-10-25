"""Real Chromium rendering against local assets and intercepted synthetic APIs only."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from test_pdf import sample_result

from atp.briefing_content import build_overview
from atp.report_notes import report_notes

ROOT = Path(__file__).resolve().parents[1] / "src" / "atp"


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


def mount(browser, payload, path, width, *, memory_enabled=False, responses=None, clock_time=None):
    page = browser.new_page(viewport={"width": width, "height": 812 if width == 375 else 800})
    if clock_time is not None:
        page.clock.install(time=clock_time)
    errors, failed, posted = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: failed.append(request.url))
    html = (
        Environment(loader=FileSystemLoader(ROOT / "templates"))
        .get_template("index.html")
        .render(config={"configured": True, "fixture_mode": False, "memory_enabled": memory_enabled})
    )

    def respond(route):
        from urllib.parse import urlsplit

        url = urlsplit(route.request.url)
        if url.path.startswith("/static/"):
            asset = (ROOT / url.path.lstrip("/")).resolve()
            assert asset.is_relative_to((ROOT / "static").resolve())
            route.fulfill(path=asset)
        elif responses and url.path in responses:
            response = responses[url.path]
            response = response(route.request) if callable(response) else response
            if isinstance(response, str):
                route.fulfill(body=response, content_type="text/html")
            else:
                status, response = response if isinstance(response, tuple) else (200, response)
                route.fulfill(json=response, status=status)
        elif url.path.endswith("/answers"):
            posted.append(route.request.post_data_json)
            route.fulfill(json={"accepted": True})
        elif url.path.startswith(("/v1/runs/", "/v1/briefings/")):
            route.fulfill(json=payload)
        elif url.path == "/v1/meetings":
            route.fulfill(json=[])
        elif url.path == path:
            route.fulfill(body=html, content_type="text/html")
        else:
            route.fulfill(status=404, body="Unknown synthetic test route")

    page.route("**/*", respond)
    page.goto("https://atp.test" + path)
    return page, posted, errors, failed


@pytest.mark.parametrize("width", [1280, 375])
def test_closed_question_choices_submit_through_domain_validation(browser, width, tmp_path):
    from test_clarification_answers import data, question

    from atp.answers import resolve_answers
    from atp.models import Clarification

    stored = [question(field=field) for field in ("goal", "relationship", "relationship_context")]
    for q in stored:
        q.update(options=[], answer_types=["provide", "select", "leave_unknown"])
    payload = {
        "status": "needs_clarification",
        "version": 2,
        "clarifications": [Clarification.model_validate(q).model_dump(mode="json") for q in stored],
    }
    saved = []

    def submit(request):
        body = request.post_data_json
        assert body["expected_version"] == 2
        changed, _, _ = resolve_answers(data(), stored, body["answers"], {}, "browser-test")
        saved.append(changed)
        payload.update(status="queued", version=3, clarifications=[])
        return {"status": "queued", "version": 3}

    page, _, errors, failed = mount(
        browser, payload, "/runs/test", width, responses={"/v1/runs/test/answers": submit}
    )
    page.get_by_role(
        "heading", name="Ist dies ein Neukontakt, ein Bestandskunde oder ein laufendes Projekt?"
    ).wait_for()
    page.locator('[id="answer-K04:goal-provide"]').fill("Wir wollen mehr Kunden gewinnen")
    assert not page.locator('[id="answer-K04:relationship-provide"]').is_visible()
    assert page.locator('[id="answer-K04:relationship-action"]').count() == 0
    page.locator('[id="answer-K04:relationship-select"]').select_option("Neukontakt")
    page.locator('[id="answer-K04:relationship_context-select"]').select_option("Neue Vertriebsanfrage")
    page.screenshot(path=str(tmp_path / f"fixed-choices-{width}.png"), full_page=True)
    page.get_by_role("button", name="Übernehmen und fortsetzen").click()
    page.wait_for_function("document.querySelector('button[type=submit]') === null")
    assert saved[0]["relationship"] == "new_contact"
    assert saved[0]["relationship_context"] == "new_sales_inquiry"
    assert saved[0]["goal"] == "Wir wollen mehr Kunden gewinnen"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors and not failed
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_only_real_questions_visible_with_partial_answers_and_explicit_unknown(browser, width, tmp_path):
    result = sample_result().model_dump(mode="json")
    result["quality"] = {"state": "limited", "reasons": ["Aktuelle Rolle offen."]}
    questions = [
        {
            "question_id": "goal",
            "code": "K04",
            "field": "goal",
            "question": "Welches Ziel hat der Termin?",
            "reason": "Das Ziel bestimmt die Empfehlung.",
            "affected_sections": ["Empfehlung"],
            "options": [],
            "answer_types": ["provide", "leave_unknown"],
            "allow_unknown": True,
        },
        {
            "question_id": "relation",
            "code": "K04",
            "field": "relationship",
            "reason": "Welche Beziehung besteht?",
            "options": ["Neukontakt", "Bestandskunde"],
        },
        {
            "question_id": "required",
            "code": "K01",
            "field": "company.name",
            "reason": "Firma ergänzen",
            "options": [],
            "answer_types": ["provide", "leave_unknown"],
            "allow_unknown": True,
        },
    ]
    payload = {
        "status": "needs_clarification",
        "version": 7,
        "clarifications": questions,
        "independent_result": result,
        "usage": {"active_seconds": 240, "search_calls": 12, "evidence_fetches": 20},
    }
    payload["clarifications"].append(
        {
            "question_id": "research",
            "code": "K06",
            "field": "employees_total",
            "question": "Wie viele Mitarbeiter hat die Firma?",
            "reason": "Beleg fehlt",
            "options": [],
        }
    )
    page, posted, errors, failed = mount(browser, payload, "/runs/test", width)
    page.get_by_role("heading", name="Welches Ziel hat der Termin?").wait_for()
    assert page.get_by_text("Das Ziel bestimmt die Empfehlung.", exact=True).count() == 0
    text = page.locator("main").inner_text()
    assert "Davon abhängig" not in text and "Betroffenes Feld" not in text
    assert result["company"][0]["text"] not in text
    assert "Bereits recherchiert" not in text and "Bestätigte Angaben" not in text
    assert "Aktive Verarbeitung" not in text and "Reservierte Suchschritte" not in text
    assert page.get_by_role("heading", name="Wie viele Mitarbeiter hat die Firma?").count() == 0
    assert page.locator(".clarification").count() == 3
    assert page.locator("#answer-required-unknown").count() == 0
    assert page.get_by_text("Antwortart", exact=True).count() == 0
    submit = page.get_by_role("button", name="Übernehmen und fortsetzen")
    submit.click()
    assert not posted
    page.locator("#answer-goal-provide").fill("Prioritäten abstimmen")
    submit.click()
    page.wait_for_function("document.querySelector('button[type=submit]').disabled === false")
    assert posted[-1] == {
        "expected_version": 7,
        "answers": {"goal": {"action": "provide", "value": "Prioritäten abstimmen"}},
    }
    page.locator("#answer-relation-select").select_option("Bestandskunde")
    page.locator("#answer-goal-unknown").check()
    assert page.locator("#answer-goal-provide").is_disabled()
    submit.click()
    page.wait_for_function("document.querySelector('button[type=submit]').disabled === false")
    assert posted[-1]["answers"] == {
        "goal": {"action": "leave_unknown", "value": ""},
        "relation": {"action": "select", "value": "Bestandskunde"},
    }
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"clarifications-{width}.png"), full_page=True)
    assert not errors and not failed
    page.close()


def test_selectable_suggestions_allow_custom_answers_and_explicit_unknown(browser):
    payload = {
        "status": "needs_clarification",
        "version": 1,
        "clarifications": [
            {
                "question_id": "goal",
                "code": "K04",
                "field": "goal",
                "question": "Was ist das Ziel?",
                "options": ["Weiterbildung", "Kundenprojekt"],
                "allow_unknown": True,
            }
        ],
    }
    page, posted, errors, failed = mount(browser, payload, "/runs/test", 375)
    choice = page.locator("#answer-goal-select")
    choice.select_option(index=3)
    page.locator("#answer-goal-provide").fill("Bedarf für einen Pilotversuch besprechen")
    page.get_by_role("button", name="Übernehmen und fortsetzen").click()
    page.wait_for_function("document.querySelector('button[type=submit]').disabled === false")
    assert posted[-1]["answers"]["goal"] == {
        "action": "provide",
        "value": "Bedarf für einen Pilotversuch besprechen",
    }
    page.locator("#answer-goal-unknown").check()
    page.get_by_role("button", name="Übernehmen und fortsetzen").click()
    page.wait_for_function("document.querySelector('button[type=submit]').disabled === false")
    assert posted[-1]["answers"]["goal"]["action"] == "leave_unknown"
    assert not errors and not failed
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_old_research_only_pause_waits_for_worker_without_question_form(browser, width, tmp_path):
    payload = {
        "status": "needs_clarification",
        "phase": "researching",
        "version": 3,
        "clarifications": [
            {
                "question_id": "research",
                "code": "K06",
                "field": "employees_total",
                "question": "Mitarbeiterzahl?",
                "reason": "Unbekannt",
                "options": [],
            }
        ],
        "usage": {"active_seconds": 240},
        "independent_result": sample_result().model_dump(mode="json"),
    }
    requests = []

    def status(request):
        requests.append(request.method)
        return payload

    page, posted, errors, failed = mount(
        browser, payload, "/runs/test", width, responses={"/v1/runs/test": status}
    )
    page.get_by_role("heading", name="Wir stellen dein Briefing fertig.").wait_for()
    assert page.locator("form").count() == 0 and not posted
    page.wait_for_timeout(1800)
    assert len(requests) >= 2  # The older pause must not freeze status polling.
    assert "Mitarbeiterzahl?" not in page.locator("main").inner_text()
    assert not errors and not failed
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"research-continues-{width}.png"), full_page=True)
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_briefing_quality_provenance_and_legacy_fallback(browser, width, tmp_path):
    result = sample_result().model_dump(mode="json")
    result["quality"] = {"state": "clarification_required", "reasons": ["Gesprächsziel offen."]}
    result["resolutions"] = {
        "q1": {
            "question": "Welche Mitarbeiterzahl?",
            "action": "leave_unknown",
            "value": "",
            "confirmed_by": "test-user",
            "confirmed_at": "2026-10-15T10:00:00Z",
        }
    }
    result["report_notes"] = report_notes(result)
    page, _, errors, failed = mount(browser, result, "/briefings/test", width)
    page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
    page.get_by_text("Recherchehinweise · Wesentliche Klärung offen", exact=True).click()
    assert page.get_by_text("Gesprächsziel offen.", exact=True).is_visible()
    page.locator("#A06").wait_for(state="visible")
    page.get_by_text("Rechercheverlauf und offene Punkte", exact=True).click()
    assert "Bewusst ungeklärt gelassen; keine Tatsachenbestätigung." in page.locator("main").inner_text()
    assert "Bestätigt von: test-user" in page.locator("main").inner_text()
    assert page.get_by_role("link", name="PDF herunterladen ↓", exact=True).is_visible()
    assert page.locator("script").count() == 2  # config + one module; claim text must stay text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"briefing-{width}.png"), full_page=True)
    result.pop("quality")
    result.pop("resolutions")
    result.pop("report_notes")
    page.reload()
    page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
    page.get_by_text("Recherchehinweise · Fachliche Verwendbarkeit nicht bewertet", exact=True).wait_for()
    assert not errors and not failed
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_new_overview_navigation_sources_and_html_download_without_pdf(browser, width, tmp_path):
    sample = sample_result()
    sample.overview = build_overview(sample)
    result = sample.model_dump(mode="json")
    result["pdf_status"] = "failed"
    page, _, errors, failed = mount(browser, result, "/briefings/test", width)
    page.get_by_role("heading", name="Unternehmen in 20 Sekunden").wait_for()
    assert page.locator(".overview-card").count() == 5
    assert page.locator(".overview-card").first.locator('a[href="#source-0"]').count() == 1
    page.get_by_text("Weitere Aktionen", exact=True).click()
    assert page.get_by_role("link", name="HTML herunterladen").get_attribute("href").endswith("/html")
    assert page.get_by_role("button", name="PDF erneut erstellen").is_visible()
    assert page.locator(".priority-question").count() == 3
    assert page.locator(".reader-supporting > details > summary").all_text_contents() == [
        "Unternehmen & Personen", "Einordnung", "Quellen & Recherchegrenzen",
    ]
    assert not page.locator("#decision").is_visible()
    assert not page.locator("#A06").is_visible()
    page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
    page.locator("#A06").wait_for(state="visible")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"overview-{width}.png"), full_page=True)
    page.screenshot(path=str(tmp_path / f"overview-viewport-{width}.png"))
    assert not errors and not failed
    page.close()


def test_mobile_research_gap_keeps_long_evidence_url_readable(browser):
    result = sample_result().model_dump(mode="json")
    evidence_url = "https://example.org/filestorage/" + "Annual_report_2026_" * 12 + ".pdf"
    reason = "Beleg nicht nutzbar (evidence_timeout): " + evidence_url
    result["research_coverage"] = [{"area": "company", "status": "partial", "reasons": [reason]}]
    page, _, errors, failed = mount(browser, result, "/briefings/test", 375)
    page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
    page.get_by_text("Rechercheverlauf und offene Punkte", exact=True).click()
    page.get_by_text(reason, exact=True).wait_for()
    page.evaluate("document.fonts.ready")
    assert page.get_by_text(reason, exact=True).inner_text() == reason
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors and not failed
    page.close()
