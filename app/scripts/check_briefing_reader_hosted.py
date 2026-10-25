"""Authenticated, read-only release check of a saved briefing and its exports.

Only a separate login session is created/revoked. No research, feedback, decision,
meeting or database changes are submitted. Proof files are private ignored output.
"""

import argparse
import hashlib
import json
from pathlib import Path

import httpx
import pymupdf
from playwright.sync_api import sync_playwright

from atp.models import BriefingResult

BASE = "https://atp.127.0.0.1.sslip.io"


def canonical(value):
    result = BriefingResult.model_validate(
        {key: data for key, data in value.items() if key in BriefingResult.model_fields}
    )
    return hashlib.sha256(result.model_dump_json().encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-file", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    account = json.loads(args.account_file.read_text(encoding="utf-8-sig"))
    original = json.loads(args.source.read_text(encoding="utf-8"))
    path = "/briefings/" + original["briefing_id"]
    args.output.mkdir(parents=True, exist_ok=True)
    checks = []
    with httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=60) as client:
        assert client.get("/v1" + path).status_code == 401
        response = client.post("/v1/auth/login", json={key: account[key] for key in ("email", "password")})
        assert response.status_code == 200, "Separate check-session login failed"
        try:
            response = client.get("/v1" + path)
            response.raise_for_status()
            saved = response.json()
            assert canonical(saved) == canonical(original), "Saved briefing changed"
            help_page = client.get("/help")
            assert help_page.status_code == 200 and "briefing-reader-1" in help_page.text
            for suffix, filename in (
                ("/pdf?compact=true", "briefing.pdf"),
                ("/pdf", "briefing-with-evidence.pdf"),
                ("/html", "briefing.html"),
            ):
                response = client.get("/v1" + path + suffix)
                response.raise_for_status()
                (args.output / filename).write_bytes(response.content)
            document = pymupdf.open(args.output / "briefing.pdf")
            pdf_text = " ".join(page.get_text() for page in document)
            assert "Die drei wichtigsten Gesprächsfragen" in pdf_text
            assert "Profil-Hash" not in pdf_text
            for index, page in enumerate(document):
                page.get_pixmap().save(args.output / f"pdf-{index + 1}.png")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
                context.add_cookies(
                    [
                        {
                            "name": cookie.name,
                            "value": cookie.value,
                            "url": BASE,
                            "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
                            "secure": cookie.secure,
                            "sameSite": "Strict",
                        }
                        for cookie in client.cookies.jar
                    ]
                )
                for width, height in ((1280, 800), (375, 812)):
                    page = context.new_page()
                    page.set_viewport_size({"width": width, "height": height})
                    page.set_default_timeout(15000)
                    errors, failed, writes = [], [], []
                    page.on("pageerror", lambda error, errors=errors: errors.append(type(error).__name__))
                    page.on(
                        "console",
                        lambda msg, errors=errors: (
                            errors.append("console-error") if msg.type == "error" else None
                        ),
                    )
                    page.on("requestfailed", lambda _, failed=failed: failed.append("request-failed"))
                    page.on(
                        "request",
                        lambda request, writes=writes: (
                            writes.append(request.method) if request.method != "GET" else None
                        ),
                    )
                    # Application readiness is the rendered briefing, not global
                    # network idleness (the application can refresh history).
                    page.goto(BASE + path, wait_until="domcontentloaded", timeout=30000)
                    page.locator(".reader-opener").wait_for()
                    page.evaluate("document.fonts.ready")
                    assert not page.get_by_label("Deine Entscheidung", exact=True).is_visible()
                    assert not page.get_by_label("Was ist richtig?", exact=True).is_visible()
                    assert page.locator(".question-list > li:visible").count() == min(
                        3, len(saved["questions"])
                    )
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                    page.screenshot(path=str(args.output / f"web-{width}.png"))
                    page.screenshot(path=str(args.output / f"web-full-{width}.png"), full_page=True)
                    page.get_by_text("Weitere Aktionen", exact=True).click()
                    assert page.get_by_role("link", name="HTML herunterladen", exact=True).is_visible()
                    actions = page.locator(".briefing-tools").bounding_box()
                    purpose = page.locator(".briefing-purpose").bounding_box()
                    assert actions["y"] + actions["height"] <= purpose["y"]
                    page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
                    assert page.locator("#A06").is_visible()
                    page.get_by_text("Entscheidung festhalten", exact=True).click()
                    assert page.get_by_label("Deine Entscheidung", exact=True).is_visible()
                    feedback_available = bool(
                        page.get_by_role("button", name="Aussage korrigieren", exact=True).count()
                    )
                    if feedback_available:
                        page.get_by_role("button", name="Aussage korrigieren", exact=True).click()
                        page.get_by_role("button", name="Korrigieren", exact=True).first.click()
                        assert page.get_by_label("Was ist richtig?", exact=True).is_visible()
                    assert not errors and not failed and not writes
                    checks.append(
                        {
                            "width": width,
                            "overflow": False,
                            "errors": errors,
                            "failed_requests": failed,
                            "writes": writes,
                            "feedback_editor_available": feedback_available,
                        }
                    )
                    page.close()
                browser.close()
            assert canonical(client.get("/v1" + path).json()) == canonical(original)
            report = {
                "status": "passed",
                "saved_briefing_unchanged": True,
                "pdf_pages": len(document),
                "surfaces": checks,
                "scope": "Real authenticated HTTPS, saved briefing, compact/full PDF and HTML. No new model run or data writes.",
            }
            (args.output / "live-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
        finally:
            client.post("/v1/auth/logout")


if __name__ == "__main__":
    main()
