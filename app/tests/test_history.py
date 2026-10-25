"""History timing and incremental UI, with synthetic browser data."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount

from atp.history import history_timing

NOW = datetime(2026, 10, 15, 14, 20, tzinfo=UTC)


def running(**overrides):
    return {"status": "researching", "phase": "researching", "job_status": "running",
            "lease_until": NOW + timedelta(seconds=90), "usage_saved_at": NOW - timedelta(seconds=20),
            "job_usage": {"active_seconds": 50}, **overrides}


@pytest.mark.parametrize("phase,expected", [
    ("validating", 300), ("researching", 300), ("evaluating", 120),
    ("generating", 60), ("exporting", 30),
])
def test_phase_and_active_budget(phase, expected):
    assert history_timing(running(phase=phase), 480, NOW) == {
        "estimated_remaining_seconds": expected, "timing_state": "running"}
    assert history_timing(running(phase=phase), 80, NOW)["estimated_remaining_seconds"] == 10
    assert history_timing(running(phase=phase), 60, NOW)["estimated_remaining_seconds"] == 0


@pytest.mark.parametrize("status,state", [
    ("queued", "queued"), ("needs_clarification", "paused"),
    ("completed", "finished"), ("partial", "finished"),
    ("failed", "finished"), ("cancelled", "finished"),
])
def test_status_overrides_stale_phase(status, state):
    assert history_timing(running(status=status), 480, NOW) == {
        "estimated_remaining_seconds": None, "timing_state": state}


def test_interruption_missing_data_and_resume():
    assert history_timing(running(lease_until=NOW), 480, NOW)["timing_state"] == "interrupted"
    assert history_timing(running(usage_saved_at=None), 480, NOW)["timing_state"] == "unavailable"
    # Resuming after a day waiting counts only persisted active time and the current segment.
    resumed = running(usage_saved_at=NOW, job_usage={"active_seconds": 450})
    assert history_timing(resumed, 480, NOW)["estimated_remaining_seconds"] == 30


def meeting(id="m1", status="researching", phase="researching", state="running", remaining=300):
    return {"meeting_id": id, "company_name": "Musterfirma " + id, "latest_run_id": id + "r2",
            "status": status, "runs": [
                {"run_id": id + "r2", "status": status, "phase": phase, "result_version": 2,
                 "created_at": NOW.isoformat(), "timing_state": state,
                 "estimated_remaining_seconds": remaining},
                {"run_id": id + "r1", "status": "completed", "result_version": 1,
                 "created_at": (NOW - timedelta(days=1)).isoformat(), "timing_state": "finished"}]}


@pytest.mark.parametrize("width", [1280, 375])
def test_history_updates_preserve_focus_details_and_render(browser, width):
    data = {"items": [meeting(), meeting("m2", "queued", state="queued"),
                      meeting("m3", "needs_clarification", state="paused"),
                      meeting("m4", state="interrupted")]}
    calls = []

    def response(request):
        calls.append(request.url)
        return data

    page, _, errors, failed = mount(browser, {}, "/history", width,
                                   responses={"/v1/meetings": response}, clock_time=NOW)
    card = page.locator(".history-card").first
    card.locator(".history-start").wait_for()
    assert "Version 2 · Gestartet: 15.10.2026, 16:20 Uhr" in card.inner_text()
    assert "Voraussichtlich noch ca. 5 Minuten" in card.inner_text()
    assert page.get_by_text("Wartet auf freien Verarbeitungsslot", exact=True).is_visible()
    assert page.get_by_text("Wartet auf deine Antwort", exact=True).is_visible()
    assert page.get_by_text("Verarbeitung unterbrochen – wartet auf Wiederaufnahme", exact=True).is_visible()
    card.locator("summary").click()
    card.get_by_role("link", name="Status öffnen →").focus()
    # The clock is installed before navigation so all application timers are controlled.
    data["items"][0]["runs"][0].update(phase="exporting", estimated_remaining_seconds=30)
    page.clock.run_for(5100)
    page.wait_for_function("document.querySelector('.history-card .tag').textContent === 'PDF erstellen'")
    assert card.locator("details").get_attribute("open") is not None
    assert card.get_by_role("link", name="Status öffnen →").evaluate("e => e === document.activeElement")
    assert "Voraussichtlich weniger als 1 Minute" in card.inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    output = Path(__file__).resolve().parents[1] / "output" / "history-overview"
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / f"history-{width}.png"), full_page=True)
    data["items"][0]["runs"][0].update(status="completed", timing_state="finished", briefing_id="briefing2")
    page.clock.run_for(5100)
    card.get_by_role("link", name="Briefing öffnen →").wait_for()
    assert card.locator(".history-estimate").inner_text() == ""
    page.get_by_role("link", name="Neue Vorbereitung", exact=True).click()
    previous = len(calls)
    page.clock.run_for(15000)
    assert len(calls) == previous
    assert not errors and not failed
    page.close()


def test_client_estimate_anchors_and_pause_reset(browser):
    page, _, errors, failed = mount(browser, {}, "/history", 1280,
                                   responses={"/v1/meetings": {"items": []}})
    result = page.evaluate("""async () => {
      const { createHistoryTiming } = await import('/static/history.js?v=history-1');
      let now = Date.parse('2026-10-15T14:20:00Z');
      const timing = createHistoryTiming(() => now);
      const run = {run_id:'r', phase:'researching', timing_state:'running', estimated_remaining_seconds:300};
      const first = timing(run);
      now += 61000;
      const second = timing(run);
      now += 240000;
      const overdue = timing(run);
      run.timing_state = 'paused'; const paused = timing(run);
      run.timing_state = 'running'; const resumed = timing(run);
      run.phase = 'generating'; run.estimated_remaining_seconds = 60;
      const generating = timing(run);
      return { first, second, overdue, paused, resumed, generating };
    }""")
    assert "5 Minuten" in result["first"] and "16:25 Uhr" in result["first"]
    assert "4 Minuten" in result["second"] and "16:25 Uhr" in result["second"]
    assert result["overdue"] == "Dauert länger als geschätzt"
    assert result["paused"] == "Wartet auf deine Antwort"
    assert "5 Minuten" in result["resumed"]
    assert "16:26 Uhr" in result["generating"]
    assert not errors and not failed
    page.close()


def test_poll_failure_keeps_cards_and_recovers(browser):
    response = {"value": {"items": [meeting()]}}
    page, _, errors, failed = mount(browser, {}, "/history", 1280,
                                   responses={"/v1/meetings": lambda _: response["value"]}, clock_time=NOW)
    page.locator(".history-card").wait_for()
    response["value"] = (503, {"message": "Synthetic temporary outage"})
    page.clock.run_for(5100)
    page.get_by_text("Aktualisierung fehlgeschlagen", exact=False).wait_for()
    assert page.locator(".history-card").count() == 1
    response["value"] = {"items": [meeting(status="completed", state="finished")]}
    page.clock.run_for(5100)
    page.wait_for_function("document.querySelector('.history-card > div > .tag').textContent === 'Erstellung abgeschlossen'")
    assert page.get_by_text("Aktualisierung fehlgeschlagen", exact=False).is_hidden()
    assert page.locator(".history-card > div > .tag").inner_text() == "Erstellung abgeschlossen"
    assert not [error for error in errors if "503" not in error] and not failed
    page.close()


def test_search_discards_late_responses_and_old_page_instances(browser):
    page, _, errors, failed = mount(browser, {}, "/history", 1280,
                                   responses={"/v1/meetings": {"items": [meeting()]}})
    page.locator(".history-card").wait_for()
    pending = []

    def respond(route):
        if "q=slow" in route.request.url:
            pending.append(route)
        else:
            route.fulfill(json={"items": [meeting("new")]})

    page.route("**/v1/meetings?*", respond)
    search = page.get_by_role("searchbox")
    search.fill("slow")
    page.get_by_role("button", name="Suchen", exact=True).click()
    page.wait_for_timeout(100)
    assert len(pending) == 1
    search.fill("new")
    page.get_by_role("button", name="Suchen", exact=True).click()
    page.get_by_role("heading", name="Musterfirma new").wait_for()
    pending.pop().fulfill(json={"items": [meeting("old")]})
    page.wait_for_timeout(100)
    assert page.get_by_role("heading", name="Musterfirma old").count() == 0
    # A detached history page must not schedule another poll after returning to history.
    search.fill("slow")
    page.get_by_role("button", name="Suchen", exact=True).click()
    page.wait_for_timeout(100)
    page.get_by_role("link", name="Neue Vorbereitung", exact=True).click()
    page.get_by_role("link", name="Historie", exact=True).click()
    page.get_by_role("heading", name="Musterfirma new").wait_for()
    pending.pop().fulfill(json={"items": [meeting("detached")]})
    page.wait_for_timeout(100)
    assert page.get_by_role("heading", name="Musterfirma detached").count() == 0
    assert not errors and not failed
    page.close()
