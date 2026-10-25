"""Hierarchy navigation and the retired project boundary, using synthetic browser data."""

from types import SimpleNamespace

import httpx
import pytest
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount
from test_http_reliability import app_with_repo

from atp.memory_models import FeedbackInput


@pytest.mark.parametrize("width", [1280, 375])
def test_hierarchy_back_and_freetext_preservation(browser, width, tmp_path):
    page, _, errors, failed = mount(browser, {}, "/", width)
    assert page.locator("#back-navigation").is_hidden()
    page.get_by_role("tab", name="Freitext", exact=True).click()
    page.get_by_label("Was steht an?").fill("Meine noch nicht abgeschickten Angaben")
    back = page.get_by_role("link", name="← Zurück", exact=True)
    assert back.bounding_box()["height"] >= 44
    back.click()
    assert page.get_by_role("tab", name="Formular", exact=True).get_attribute("aria-selected") == "true"
    page.get_by_role("tab", name="Freitext", exact=True).click()
    assert page.get_by_label("Was steht an?").input_value() == "Meine noch nicht abgeschickten Angaben"
    page.get_by_role("link", name="Historie", exact=True).click()
    assert back.get_attribute("href") == "/"
    page.screenshot(path=str(tmp_path / f"back-history-{width}.png"), full_page=True)
    back.click()
    # Returning to the start preserves the selected input mode; its own parent is the form.
    back.click()
    assert page.locator("#back-navigation").is_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors and not failed
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_learning_history_and_parent_return(browser, width, tmp_path):
    responses = {
        "/v1/contexts": {"items": []},
        "/v1/memory/tasks": {
            "items": [
                {"id": "l1", "kind": "learning", "created_at": "2026-10-15T12:00:00Z", "status": "queued"}
            ]
        },
        "/v1/memory/tasks/l1": {"id": "l1", "kind": "learning", "context_id": "c1", "status": "queued"},
        "/v1/contexts/c1": {
            "id": "c1",
            "kind": "project",
            "name": "Mein Kontext",
            "description": "",
            "entries": [],
            "feedback": [],
            "shares": [],
            "owned": False,
            "editable": False,
        },
    }
    page, _, errors, failed = mount(
        browser, {}, "/knowledge", width, memory_enabled=True, responses=responses
    )
    page.get_by_role("heading", name="Learning-Vorschläge", exact=True).wait_for()
    assert page.get_by_role("link", name="Projektbriefing", exact=True).count() == 0
    page.get_by_role("link", name="Learning-Vorschlag ·", exact=False).click()
    page.get_by_role("heading", name="Vorbereitung läuft", exact=True).wait_for()
    assert page.get_by_role("link", name="← Zurück", exact=True).get_attribute("href") == "/knowledge/c1"
    page.screenshot(path=str(tmp_path / f"learning-back-{width}.png"), full_page=True)
    page.get_by_role("link", name="← Zurück", exact=True).click()
    page.get_by_role("heading", name="Mein Kontext", exact=True).wait_for()
    page.get_by_role("link", name="← Zurück", exact=True).click()
    page.get_by_role("heading", name="Learning-Vorschläge", exact=True).wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors and not failed
    page.close()


@pytest.mark.parametrize("path", ["/briefings/missing", "/runs/missing"])
def test_error_keeps_return_to_history(browser, path):
    page, _, errors, _ = mount(
        browser,
        {},
        path,
        1280,
        responses={"/v1" + path: (404, {"message": "Nicht gefunden", "request_id": "synthetic"})},
    )
    page.get_by_role("alert").wait_for()
    assert page.get_by_role("link", name="← Zurück", exact=True).get_attribute("href") == "/history"
    assert not [e for e in errors if "404" not in e]
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_preview_back_preserves_confirmed_input(browser, width):
    page, _, errors, failed = mount(browser, {}, "/", width)
    page.get_by_label("Firmenname", exact=True).fill("Musterwerk GmbH")
    page.get_by_label("Website", exact=True).fill("https://musterwerk.example")
    page.get_by_label("Name", exact=True).fill("Lea Sommer")
    page.get_by_label("Meeting-Anlass", exact=True).select_option("project_review")
    page.locator("details.optional-section summary").filter(has_text="Gesprächsdetails").click()
    page.get_by_label("Gesprächsziel", exact=False).fill("Entscheidungen klären")
    page.get_by_role("button", name="Angaben prüfen", exact=False).click()
    page.get_by_role("heading", name="Einmal prüfen. Dann recherchieren.").wait_for()
    page.get_by_role("link", name="← Zurück", exact=True).click()
    assert page.get_by_label("Firmenname", exact=True).input_value() == "Musterwerk GmbH"
    assert page.get_by_label("Meeting-Anlass", exact=True).input_value() == "project_review"
    assert page.get_by_label("Gesprächsziel", exact=False).input_value() == "Entscheidungen klären"
    assert not errors and not failed
    page.close()


async def test_removed_routes_and_legacy_page_redirect():
    app = app_with_repo(SimpleNamespace())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        assert (await client.post("/v1/project-preparations", json={})).status_code == 404
        assert (
            await client.get("/v1/memory/tasks/00000000-0000-0000-0000-000000000001/pdf")
        ).status_code == 404
        response = await client.get("/projects?context=old")
        assert response.status_code == 307 and response.headers["location"] == "/knowledge"


def test_feedback_cannot_reference_retired_project():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FeedbackInput(
            context_id="00000000-0000-0000-0000-000000000001",
            text="Feedback",
            task_id="00000000-0000-0000-0000-000000000002",
        )
