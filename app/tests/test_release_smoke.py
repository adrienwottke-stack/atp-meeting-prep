"""Hosted smoke harness with intercepted HTTP only; never contacts live services."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_pdf import sample_result

from atp.briefing_content import build_overview

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_smoke.py"
spec = importlib.util.spec_from_file_location("release_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
BASE = "https://smoke.invalid"
ACCOUNT = {"email": "synthetic@example.invalid", "password": "private-test-password"}
MEETING = "f73b4b99-78f1-4c1e-8190-34b79ae12b3e"
RUN = "37a2a198-452f-41a8-a661-66b2a81e32b5"
BRIEFING = "3d91efb4-0e2f-40dd-b555-1036f271ee1e"


def terminal(status="partial"):
    result = sample_result()
    result.overview = build_overview(result)
    data = result.model_dump(mode="json")
    data.update(status=status, fixture_mode=False, quality={"state": "limited", "reasons": ["Rolle offen."]})
    return data


class Harness:
    def __init__(self, runs, *, result=None, pdf_ok=True):
        self.runs = list(runs)
        self.result = result or terminal()
        self.pdf_ok = pdf_ok
        self.requests = []
        self.answers = []
        self.deleted = []
        self.logged_in = False
        self.preparation = None
        self.seconds = 0
        self.client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(self.handle))

    def sleep(self, seconds):
        assert 0 <= seconds <= 3
        self.seconds += seconds

    def handle(self, request):
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "fixture_mode": False})
        if request.url.path == "/v1/meetings":
            return httpx.Response(
                401, json={"request_id": "req-synthetic"}, headers={"X-Request-ID": "req-synthetic"}
            )
        if request.url.path == "/v1/auth/login":
            assert json.loads(request.content) == ACCOUNT
            self.logged_in = True
            return httpx.Response(
                200,
                json={"authenticated": True},
                headers={"Set-Cookie": "atp_access=fake-session; Path=/; Secure; HttpOnly; SameSite=Strict"},
            )
        if request.url.path == "/v1/auth/logout":
            self.logged_in = False
            return httpx.Response(
                200, json={"authenticated": False}, headers={"Set-Cookie": "atp_access=; Path=/; Max-Age=0"}
            )
        if request.url.path == "/v1/auth/me":
            return httpx.Response(200, json={"role": "admin"})
        if request.url.path == "/v1/preparations":
            if request.headers.get("origin") != BASE:
                return httpx.Response(
                    403, json={"request_id": "req-synthetic"}, headers={"X-Request-ID": "req-synthetic"}
                )
            assert request.headers.get("idempotency-key")
            self.preparation = json.loads(request.content)
            return httpx.Response(202, json={"meeting_id": MEETING, "run_id": RUN})
        if request.url.path == "/help":
            return httpx.Response(200, text="<h1>Hilfe</h1>", headers={"Content-Type": "text/html"})
        if request.url.path == "/v1/handbook/pdf":
            return httpx.Response(200, content=b"%PDF-synthetic handbook")
        if request.url.path == "/v1/runs/" + RUN:
            return httpx.Response(200, json=self.runs.pop(0) if len(self.runs) > 1 else self.runs[0])
        if request.url.path == "/v1/runs/" + RUN + "/answers":
            self.answers.append(json.loads(request.content))
            return httpx.Response(200, json={"accepted": True})
        if request.url.path == "/v1/briefings/" + BRIEFING:
            return httpx.Response(200, json=self.result)
        if request.url.path == "/v1/briefings/" + BRIEFING + "/pdf":
            return httpx.Response(200 if self.pdf_ok else 500, content=b"%PDF-synthetic briefing")
        if request.url.path == "/v1/briefings/" + BRIEFING + "/html":
            return httpx.Response(
                200,
                text="<!doctype html><h1>Briefing</h1>",
                headers={"Content-Security-Policy": "default-src 'none'"},
            )
        if request.url.path == "/v1/meetings/" + MEETING and request.method == "DELETE":
            self.deleted.append(MEETING)
            return httpx.Response(202, json={"deleted": True})
        raise AssertionError("Unexpected request in isolated harness")

    def run(self, output, **kwargs):
        return smoke.run_smoke(
            BASE,
            ACCOUNT,
            output,
            client=self.client,
            browser=False,
            now=lambda: self.seconds,
            sleep=self.sleep,
            **kwargs,
        )


def done():
    return {
        "status": "partial",
        "briefing_id": BRIEFING,
        "usage": {
            "active_seconds": 123,
            "search_trace": [{"private": "private-test-password"}],
            "errors": ["private-test-password"],
        },
    }


def question(code="K03", allowed=True):
    return {
        "status": "needs_clarification",
        "version": 1,
        "clarifications": [
            {"question_id": "q1", "code": code, "allow_unknown": allowed, "answer_types": ["leave_unknown"]}
        ],
    }


def test_partial_result_two_rounds_downloads_and_own_cleanup(tmp_path):
    second = question("K04")
    second["version"] = 2
    harness = Harness([question(), second, done()])
    output = tmp_path / "smoke"
    report = harness.run(output)
    assert report["status"] == "passed" and report["clarification_rounds"] == 2
    assert harness.answers == [
        {"expected_version": 1, "answers": {"q1": {"action": "leave_unknown", "value": ""}}},
        {"expected_version": 2, "answers": {"q1": {"action": "leave_unknown", "value": ""}}},
    ]
    assert harness.preparation["company"]["name"].startswith("TRUMPF")
    assert len(harness.preparation["people"]) == 2
    assert harness.preparation["meeting_type"] == "setup" and harness.preparation["goal"]
    assert harness.deleted == [MEETING] and not harness.logged_in
    assert (output / "briefing.pdf").read_bytes().startswith(b"%PDF-")
    assert (output / "briefing.html").read_text().startswith("<!doctype html")
    raw = (output / "report.json").read_text()
    assert ACCOUNT["password"] not in raw and ACCOUNT["email"] not in raw and "fake-session" not in raw
    assert report["usage"] == {"active_seconds": 123}


@pytest.mark.parametrize("code,allowed", [("K01", True), ("K02", False)])
def test_required_or_forbidden_questions_never_auto_answered(tmp_path, code, allowed):
    harness = Harness([question(code, allowed)])
    report = harness.run(tmp_path / "smoke")
    assert report["status"] == "failed"
    assert report["failed_check"] == "clarification_can_be_left_unknown"
    assert not harness.answers and harness.deleted == [MEETING] and not harness.logged_in


def test_repeated_question_version_does_not_repeat_answer(tmp_path):
    harness = Harness([question(), question(), done()])
    report = harness.run(tmp_path / "smoke")
    assert report["status"] == "passed" and len(harness.answers) == 1


def test_timeout_is_bounded_and_still_cleans_own_meeting(tmp_path):
    harness = Harness([{"status": "researching"}])
    report = harness.run(tmp_path / "smoke", max_wait=6)
    assert report["status"] == "failed" and report["seconds"] == 6
    assert harness.deleted == [MEETING] and not harness.logged_in


@pytest.mark.parametrize("failure", ["pdf", "references"])
def test_download_or_reference_failure_cannot_skip_cleanup(tmp_path, failure):
    result = terminal()
    if failure == "references":
        result["overview"][0]["evidence_ids"] = ["missing"]
    harness = Harness([done()], result=result, pdf_ok=failure != "pdf")
    report = harness.run(tmp_path / "smoke")
    assert report["status"] == "failed"
    assert harness.deleted == [MEETING] and not harness.logged_in


def test_output_directory_must_be_new(tmp_path):
    harness = Harness([done()])
    with pytest.raises(FileExistsError):
        harness.run(tmp_path)
    assert not harness.requests


def test_browser_proof_is_called_before_deletion(tmp_path, monkeypatch):
    harness = Harness([done()])
    calls = []

    def browser(client, base, briefing_id, output):
        assert not harness.deleted and harness.logged_in
        assert json.loads((output / "briefing.json").read_text())["overview"]
        calls.append(briefing_id)
        return {"widths": [1280, 375]}

    monkeypatch.setattr(smoke, "browser_proof", browser)
    report = smoke.run_smoke(
        BASE,
        ACCOUNT,
        tmp_path / "smoke",
        client=harness.client,
        now=lambda: harness.seconds,
        sleep=harness.sleep,
    )
    assert report["status"] == "passed" and calls == [BRIEFING]
    assert harness.deleted == [MEETING]


def test_explicit_queue_allowance_can_exceed_default_650_seconds(tmp_path):
    harness = Harness([{"status": "queued"}] * 230 + [done()])
    report = harness.run(tmp_path / "smoke", max_wait=1200)
    assert report["status"] == "passed" and report["seconds"] == 690
    assert report["max_wait_seconds"] == 1200 and report["observed_queued_seconds"] == 690
    assert harness.deleted == [MEETING] and not harness.logged_in


def test_default_wall_limit_remains_650_seconds(tmp_path):
    harness = Harness([{"status": "queued"}])
    report = harness.run(tmp_path / "smoke")
    assert report["status"] == "failed" and report["seconds"] == 650
    assert report["max_wait_seconds"] == 650 and harness.deleted == [MEETING]


@pytest.mark.parametrize("seconds", [0, -1, 1301])
def test_invalid_poll_limit_rejected_before_any_requests(tmp_path, seconds):
    harness = Harness([done()])
    with pytest.raises(smoke.SmokeFailure, match="bounded_poll_window"):
        harness.run(tmp_path / "smoke", max_wait=seconds)
    assert not harness.requests and not (tmp_path / "smoke").exists()


def test_poll_progress_is_periodic_sanitized_and_persisted(tmp_path, capsys):
    run = {
        "status": "queued",
        "phase": ACCOUNT["password"],
        "usage": {
            "active_seconds": 323,
            "model_requests": 4,
            "input_tokens": -1,
            "output_tokens": True,
            "search_trace": [ACCOUNT["password"]],
        },
    }
    harness = Harness([run] * 6 + [done()])
    output = tmp_path / "smoke"
    report = harness.run(output)
    assert report["status"] == "passed"
    lines = capsys.readouterr().out.splitlines()
    progress = [json.loads(line) for line in lines]
    assert [item["wall_seconds"] for item in progress] == [0, 15, 18]
    assert progress[0]["status"] == "queued" and progress[0]["phase"] == "unknown"
    assert progress[0]["active_seconds"] == 323 and progress[0]["model_requests"] == 4
    assert "input_tokens" not in progress[0] and "output_tokens" not in progress[0]
    assert ACCOUNT["password"] not in "\n".join(lines) and "fake-session" not in "\n".join(lines)
    assert json.loads((output / "progress.json").read_text()) == progress[-1]


def test_nonfinite_usage_cannot_reach_progress_json():
    assert smoke.numeric_usage(
        {"usage": {"active_seconds": float("inf"), "input_tokens": float("nan"), "search_calls": 2}}
    ) == {"search_calls": 2}


@pytest.mark.parametrize("failure_stage", ["goto", "cards"])
def test_browser_timeout_saves_stage_errors_http_and_screenshot_before_rethrow(
    tmp_path, monkeypatch, failure_stage
):
    import playwright.sync_api

    class Page:
        url = BASE + "/briefings/" + BRIEFING

        def __init__(self):
            self.callbacks = {}

        def on(self, event, callback):
            self.callbacks[event] = callback

        def set_default_timeout(self, timeout):
            assert timeout == 5000

        def set_viewport_size(self, viewport):
            pass

        def goto(self, url, **kwargs):
            self.callbacks["response"](
                SimpleNamespace(
                    url=self.url + "?private=must-not-appear",
                    status=200,
                    request=SimpleNamespace(resource_type="fetch"),
                    headers={"content-type": "application/json", "set-cookie": "fake-session"},
                )
            )
            self.callbacks["pageerror"](
                Exception("TypeError: reading undefined fake-session synthetic@example.invalid")
            )
            if failure_stage == "goto":
                raise TimeoutError("controlled")

        def locator(self, selector):
            def wait(**kwargs):
                raise TimeoutError("controlled")

            return SimpleNamespace(
                first=SimpleNamespace(wait_for=wait),
                count=lambda: 0,
                all_inner_texts=lambda: ["TypeError: reading undefined fake-session"],
            )

        def screenshot(self, *, path, **kwargs):
            Path(path).write_bytes(b"controlled screenshot placeholder")

    page = Page()
    context = SimpleNamespace(add_cookies=lambda cookies: None, new_page=lambda: page, close=lambda: None)
    browser = SimpleNamespace(new_context=lambda **kwargs: context, close=lambda: None)

    class Playwright:
        chromium = SimpleNamespace(launch=lambda **kwargs: browser)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", Playwright)
    client = httpx.Client()
    client.cookies.set("atp_access", "fake-session")
    with pytest.raises(TimeoutError):
        smoke.browser_proof(client, BASE, BRIEFING, tmp_path)
    diagnostic = json.loads((tmp_path / "browser-diagnostics.json").read_text())
    assert diagnostic["stage"] == failure_stage and diagnostic["error_type"] == "TimeoutError"
    assert diagnostic["responses"] == [
        {"path": "/briefings/<id>", "status": 200, "resource_type": "fetch", "mime": "application/json"}
    ]
    assert diagnostic["overview_cards"] == 0 and diagnostic["page_errors"] and diagnostic["alerts"]
    assert (tmp_path / "browser-failure.png").exists()
    raw = json.dumps(diagnostic)
    assert "fake-session" not in raw and "synthetic@example.invalid" not in raw
    assert "must-not-appear" not in raw and "set-cookie" not in raw
    client.close()
