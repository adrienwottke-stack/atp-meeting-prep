"""Native modules retain app and Memory navigation without shared globals."""

import pytest
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount


@pytest.mark.parametrize("width", [1280, 375])
def test_knowledge_creation_uses_explicit_services_and_json_body(browser, width, tmp_path):
    posted = []
    context = {
        "id": "c2",
        "name": "Testprojekt",
        "kind": "project",
        "description": "Synthetischer Kontext",
        "owned": True,
        "editable": True,
        "expires_at": "2027-01-01T00:00:00Z",
        "entries": [],
        "feedback": [],
        "shares": [],
        "external_id": "",
    }

    def contexts(request):
        if request.method == "POST":
            posted.append((request.post_data_json, request.headers.get("content-type")))
            return context
        return {"items": [context]}

    responses = {
        "/v1/memory/tasks": {"items": []},
        "/v1/contexts": contexts,
        "/v1/contexts/c2": context,
        "/help": "<!doctype html><h1>ATP Hilfe</h1>",
    }
    page, _, errors, failed = mount(
        browser, {}, "/knowledge", width, memory_enabled=True, responses=responses
    )
    page.get_by_role("heading", name="Was beim nächsten Gespräch zählt.").wait_for()
    assert page.evaluate("typeof window.renderKnowledge === 'undefined' && typeof window.api === 'undefined'")
    assert page.locator('script[type="module"]').count() == 1
    assert page.get_by_role("link", name="Hilfe", exact=True).get_attribute("data-nav") is None
    assert page.get_by_role("link", name="Projektbriefing", exact=True).count() == 0
    page.get_by_label("Name", exact=True).fill("Testprojekt")
    page.get_by_role("button", name="Kontext anlegen", exact=True).click()
    page.get_by_role("heading", name="Testprojekt", exact=True).wait_for()
    assert posted[0] == (
        {"name": "Testprojekt", "kind": "project", "description": "", "external_id": ""},
        "application/json",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(tmp_path / f"knowledge-{width}.png"), full_page=True)
    page.get_by_role("link", name="Hilfe", exact=True).click()
    page.get_by_role("heading", name="ATP Hilfe").wait_for()
    assert page.url == "https://atp.test/help"
    assert not errors and not failed
    page.close()


def test_memory_503_stays_on_page_with_request_id(browser):
    page, _, errors, failed = mount(
        browser,
        {},
        "/knowledge",
        1280,
        memory_enabled=True,
        responses={
            "/v1/contexts": (
                503,
                {"message": "Dienst vorübergehend nicht erreichbar.", "request_id": "req-synthetic"},
            )
        },
    )
    page.get_by_role("alert").wait_for()
    assert "req-synthetic" in page.get_by_role("alert").inner_text()
    assert page.url.endswith("/knowledge")
    # Chromium reports the deliberately injected HTTP 503 at console level error.
    assert not [error for error in errors if "503" not in error]
    assert not failed
    page.close()


def test_disabled_memory_input_and_legacy_form_work_without_memory_requests(browser):
    page, _, errors, failed = mount(browser, {}, "/", 1280)
    page.get_by_role("heading", name="Gespräch vorbereiten", exact=True).wait_for()
    assert page.get_by_role("link", name="Wissen & Learnings").count() == 0
    assert page.get_by_role("link", name="Projektbriefing", exact=True).count() == 0
    assert page.locator("input").count() > 0
    assert not errors and not failed
    page.close()
