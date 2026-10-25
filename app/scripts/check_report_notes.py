"""Render a saved briefing through local API/UI/PDF with isolated in-memory services.

No database, model provider, production worker or external writes are used.
"""

import argparse
import json
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from atp.config import Settings
from atp.models import BriefingResult
from atp.pdf import render_pdf
from atp.report_notes import report_notes
from atp.web import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = json.loads(args.source.read_text(encoding="utf-8"))
    original = deepcopy(raw)
    args.output.mkdir(parents=True, exist_ok=True)
    port = 8127
    base = f"http://127.0.0.1:{port}"
    app = create_app(
        Settings(
            _env_file=None,
            fixture_mode=True,
            app_base_url=base,
            database_url="isolated-test",
            worker_database_url="isolated-test",
            supabase_url=base,
            supabase_anon_key="isolated-test",
            memory_enabled=False,
        )
    )

    async def authenticate(token):
        assert token == "isolated-preview"
        return SimpleNamespace(user_id="preview", role="admin")

    app.state.auth = SimpleNamespace(authenticate=authenticate)
    app.state.storage = SimpleNamespace()
    app.state.repo = SimpleNamespace(
        get_briefing=lambda *_: deepcopy(raw),
        list_meetings=lambda *_: [],
        get_run=lambda *_: {"run_id": raw["run_id"], "independent_result": deepcopy(raw)},
    )
    with TestClient(app) as client:
        client.cookies.set("atp_access", "isolated-preview")
        response = client.get("/v1/briefings/" + raw["briefing_id"])
        assert response.status_code == 200
        assert response.json()["gaps"] == raw["gaps"]
        assert response.json()["report_notes"] == report_notes(raw)
        preview = client.get("/v1/runs/" + raw["run_id"]).json()["independent_result"]
        assert preview["report_notes"] == report_notes(raw)
    result = BriefingResult.model_validate({k: v for k, v in raw.items() if k in BriefingResult.model_fields})
    pdf = render_pdf(result)
    (args.output / "briefing-reviewed.pdf").write_bytes(pdf)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for page in doc:
        if "Quellen und Wissenslücken" in page.get_text():
            page.get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4)).save(args.output / "pdf-notes.png")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    assert httpx.get(base + "/health", trust_env=False, timeout=5).status_code == 200
    errors, failed, checks = [], [], []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
            context.add_cookies([{"name": "atp_access", "value": "isolated-preview", "url": base}])
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console", lambda message: errors.append(message.text) if message.type == "error" else None
            )
            page.on("requestfailed", lambda request: failed.append(request.url))
            for name, width, height in (("desktop", 1280, 900), ("mobile", 375, 812)):
                page.set_viewport_size({"width": width, "height": height})
                page.goto(base + "/briefings/" + raw["briefing_id"], wait_until="domcontentloaded")
                notes = page.locator("#A06")
                notes.wait_for()
                text = notes.inner_text()
                assert "evidence_http" not in text and "is_corporation:" not in text
                assert "locations:" not in text
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                notes.screenshot(path=str(args.output / f"{name}-notes.png"))
                # Source navigation still reaches the cited evidence after the notes.
                page.locator('a[href="#source-0"]').first.click()
                page.locator("#source-0").wait_for(state="visible")
                assert page.locator("#source-0").is_visible()
                checks.append({"viewport": name, "width": width, "overflow": False, "source_link": True})
            page.set_viewport_size({"width": 1280, "height": 900})
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert raw == original
    assert not errors and not failed, (errors, failed)
    report = {
        "checks": checks,
        "console_errors": errors,
        "failed_requests": failed,
        "notes": report_notes(raw),
        "raw_unchanged": True,
        "scope": "isolated local rendering; no new research",
    }
    (args.output / "review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": "passed", "checks": checks, "raw_unchanged": True}))


if __name__ == "__main__":
    main()
