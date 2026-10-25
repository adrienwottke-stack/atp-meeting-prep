"""Desktop/mobile interactions against real assets and, separately, the actual local API/DB."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_briefing_feedback import feedback_case as feedback_case
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount
from test_pdf import sample_result

from atp.briefing_content import presentation_overview


def briefing():
    result = sample_result()
    payload = result.model_dump(mode="json")
    payload["overview"] = [item.model_dump(mode="json") for item in presentation_overview(result)]
    return payload


@pytest.mark.parametrize("width", [1280, 375])
def test_feedback_works_without_context_and_survives_reload(browser, width, tmp_path):
    payload = briefing()
    base = "/v1/briefings/" + payload["briefing_id"] + "/feedback"
    items, posts = [], []

    def feedback(request):
        if request.method == "POST":
            body = request.post_data_json
            posts.append(body)
            saved = {
                **body,
                "id": body["submission_id"],
                "context_id": "private-company",
                "remembered": body["remember"],
                "created_at": "2026-10-15T18:00:00Z",
            }
            items.insert(0, saved)
            return 201, saved
        return {"items": items, "editable": True}

    page, _, errors, failed = mount(
        browser,
        payload,
        "/briefings/" + payload["briefing_id"],
        width,
        memory_enabled=True,
        responses={"/v1/memory/status": {"enabled": True}, base: feedback},
    )
    page.locator(".reader-follow-up").wait_for()
    assert not page.get_by_label("Deine Entscheidung", exact=True).is_visible()
    assert not page.get_by_label("Was ist richtig?", exact=True).is_visible()
    page.get_by_text("Entscheidung festhalten", exact=True).click()
    assert not page.get_by_text("Keine bestätigten Wissenseinträge ausgewählt.").count()
    assert not page.get_by_label("Begründung bei abweichender Entscheidung").is_visible()
    page.get_by_label("Deine Entscheidung", exact=True).select_option("Gespräch wahrnehmen")
    assert page.get_by_label("Begründung bei abweichender Entscheidung").is_visible()
    assert page.get_by_label("Begründung bei abweichender Entscheidung").get_attribute("required") is not None
    claim = page.locator(".overview-card p").first.text_content()
    page.get_by_role("button", name="Aussage korrigieren", exact=True).click()
    page.get_by_role("button", name="Korrigieren", exact=True).first.click()
    assert page.get_by_label("Aussage aus dem Briefing", exact=False).input_value() == claim[:300]
    malicious = '<script>alert("x")</script> Frau Sommer leitet den Einkauf.'
    page.get_by_label("Was ist richtig?", exact=True).fill(malicious)
    page.get_by_role("button", name="Speichern und merken", exact=True).click()
    page.locator(".feedback-entry").wait_for()
    assert posts[0]["remember"] and posts[0]["reference"] == claim[:300]
    assert page.locator(".feedback-correction-note").first.text_content().endswith(malicious)
    assert page.locator(".feedback-entry script").count() == 0
    assert page.locator(".feedback-entry").first.inner_text().find(malicious) >= 0
    assert page.get_by_label("Was ist richtig?", exact=True).input_value() == ""
    page.reload()
    page.locator(".feedback-entry").wait_for(state="attached")
    assert page.locator(".feedback-correction-note").count() == 1
    assert page.locator(".feedback-entry").count() == 1
    page.locator(".reader-action > summary").filter(has_text="Feedback festhalten").click()
    page.get_by_label("Was möchtest du festhalten?", exact=True).select_option("outcome")
    page.get_by_label("Was kam im Gespräch heraus?", exact=True).fill("Einkauf zum nächsten Termin einladen.")
    page.get_by_role("button", name="Nur hier festhalten", exact=True).click()
    page.wait_for_function("document.querySelectorAll('.feedback-entry').length === 2")
    assert not posts[1]["remember"]
    assert page.get_by_role("button", name="Für nächste Vorbereitung merken", exact=True).count() == 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.get_by_role("button", name="Speichern und merken", exact=True).bounding_box()["height"] >= 44
    page.screenshot(path=str(tmp_path / f"feedback-{width}.png"), full_page=True)
    assert not errors and not failed
    page.close()


def test_failed_save_preserves_draft_and_retry_key(browser):
    payload = briefing()
    base = "/v1/briefings/" + payload["briefing_id"] + "/feedback"
    posts = []

    def save(request):
        if request.method == "GET":
            return {"items": [], "editable": True}
        body = request.post_data_json
        posts.append(body)
        if len(posts) == 1:
            return 503, {"message": "Speicherung gerade nicht möglich."}
        return {**body, "id": body["submission_id"], "context_id": "company", "remembered": True}

    page, _, errors, failed = mount(
        browser,
        payload,
        "/briefings/" + payload["briefing_id"],
        1280,
        memory_enabled=True,
        responses={"/v1/memory/status": {"enabled": True}, base: save},
    )
    page.locator(".reader-action > summary").filter(has_text="Feedback festhalten").click()
    text = page.get_by_label("Was ist richtig?", exact=True)
    text.fill("Korrektur bleibt beim Fehler erhalten.")
    page.get_by_role("button", name="Speichern und merken", exact=True).click()
    page.get_by_text("Speicherung gerade nicht möglich.", exact=True).wait_for()
    assert text.input_value() == "Korrektur bleibt beim Fehler erhalten."
    page.get_by_role("button", name="Speichern und merken", exact=True).click()
    page.locator(".feedback-entry").wait_for()
    assert len(posts) == 2 and posts[0]["submission_id"] == posts[1]["submission_id"]
    assert not failed
    # The intentional HTTP 503 is expected in Chromium's error console.
    assert all("503" in error for error in errors)
    page.close()


@pytest.mark.parametrize("width", [1280, 375])
def test_real_browser_feedback_uses_actual_api_and_postgres(browser, feedback_case, width, tmp_path):
    c = feedback_case
    with TestClient(c.app) as client:

        def api_response(request):
            response = client.request(
                request.method,
                request.url.replace("https://atp.test", ""),
                content=request.post_data,
                headers={
                    "Authorization": "Bearer " + c.actors[0].user_id,
                    "Content-Type": "application/json",
                },
            )
            return response.status_code, response.json()

        routes = {
            "/v1/memory/status": api_response,
            "/v1/briefings/" + c.result["briefing_id"]: api_response,
            c.base: api_response,
        }
        page, _, errors, failed = mount(
            browser,
            c.result,
            "/briefings/" + c.result["briefing_id"],
            width,
            memory_enabled=True,
            responses=routes,
        )
        page.locator(".reader-action > summary").filter(has_text="Feedback festhalten").click()
        page.get_by_label("Was möchtest du festhalten?", exact=True).select_option("outcome")
        page.get_by_label("Was kam im Gespräch heraus?", exact=True).fill(
            "Im Folgegespräch den Einkauf dazunehmen und den Angebotsprozess gemeinsam durchgehen."
        )
        page.get_by_role("button", name="Speichern und merken", exact=True).click()
        page.locator(".feedback-entry").wait_for()
        page.reload()
        page.locator(".feedback-entry").wait_for(state="attached")
        page.locator(".reader-action > summary").filter(has_text="Feedback festhalten").click()
        page.locator("#briefing-feedback").scroll_into_view_if_needed()
        proof = Path(__file__).parents[1] / "output" / "briefing-feedback"
        proof.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(proof / f"feedback-{width}.png"))
        page.screenshot(path=str(proof / f"feedback-full-{width}.png"), full_page=True)
        created = c.repo.create_preparation(c.actors[0], c.data, c.profile, str(uuid4()))
        snapshot = c.admin.execute(
            "select memory_snapshot from atp.runs where id=%s", (created["run_id"],)
        ).fetchone()["memory_snapshot"]
        assert len(snapshot["entries"]) == 1 and "Einkauf dazunehmen" in snapshot["entries"][0]["text"]
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors and not failed
        (proof / f"qa-{width}.json").write_text(
            json.dumps(
                {
                    "viewport": width,
                    "overflow": False,
                    "console_errors": errors,
                    "failed_requests": failed,
                    "saved_feedback": 1,
                    "next_run_memory_entries": len(snapshot["entries"]),
                    "scope": "Real UI and FastAPI/Postgres with isolated synthetic users. No external model call.",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        page.close()
