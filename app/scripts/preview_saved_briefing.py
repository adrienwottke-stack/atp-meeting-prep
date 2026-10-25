"""Render an existing local result through the real UI, without live APIs."""

import argparse
import json
from pathlib import Path

import pymupdf
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

from atp.briefing_content import attention_notes, presentation_overview, reader_result
from atp.html_export import render_html
from atp.models import BriefingResult
from atp.pdf import render_pdf
from atp.report_notes import report_notes


def preview(result_path, output_path):
    raw = json.loads(Path(result_path).read_text(encoding="utf-8"))
    result = BriefingResult.model_validate(
        {key: value for key, value in raw.items() if key in BriefingResult.model_fields}
    )
    view = reader_result(result)
    root = Path(__file__).resolve().parents[1] / "src" / "atp"
    output = Path(output_path).resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = view.model_dump(mode="json")
    payload["report_notes"] = report_notes(payload)
    payload["reader_view"] = {
        "overview": [i.model_dump(mode="json") for i in presentation_overview(view)],
        "attention": attention_notes(view),
    }
    html = (
        Environment(loader=FileSystemLoader(root / "templates"))
        .get_template("index.html")
        .render(config={"configured": True, "fixture_mode": result.fixture_mode, "memory_enabled": False})
    )
    (output / "briefing.html").write_text(render_html(result), encoding="utf-8")
    (output / "briefing.pdf").write_bytes(render_pdf(result, compact=True))
    (output / "briefing-mit-nachweisen.pdf").write_bytes(render_pdf(result))
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width, height in [(1280, 800), (375, 812)]:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors, failed = [], []
            page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
            page.on(
                "console", lambda msg, errors=errors: errors.append(msg.text) if msg.type == "error" else None
            )
            page.on("requestfailed", lambda request, failed=failed: failed.append(request.url))

            def route(request_route):
                from urllib.parse import urlsplit

                path = urlsplit(request_route.request.url).path
                if path.startswith("/static/"):
                    asset = (root / path.lstrip("/")).resolve()
                    if not asset.is_relative_to(root / "static"):
                        raise ValueError("Asset path outside static directory")
                    request_route.fulfill(path=asset)
                elif path == "/v1/briefings/preview":
                    request_route.fulfill(json=payload)
                elif path == "/briefings/preview":
                    request_route.fulfill(body=html, content_type="text/html")
                else:
                    request_route.fulfill(json={})

            page.route("https://atp-preview.test/**", route)
            page.goto("https://atp-preview.test/briefings/preview")
            page.get_by_role("heading", name="Die wichtigsten Gesprächsfragen").wait_for()
            page.screenshot(path=str(output / f"web-{width}.png"))
            page.screenshot(path=str(output / f"web-full-{width}.png"), full_page=True)
            assert not page.locator("#A06").is_visible()
            assert page.locator(".question-list > li:visible").count() == min(3, len(result.questions))
            page.get_by_text("Quellen & Recherchegrenzen", exact=True).click()
            page.locator("#A06").wait_for(state="visible")
            page.screenshot(path=str(output / f"sources-{width}.png"))
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            assert not overflow and not errors and not failed
            checks.append({"width": width, "overflow": overflow, "errors": errors, "failed_requests": failed})
            page.close()
        browser.close()
    doc = pymupdf.open(output / "briefing.pdf")
    for index, page in enumerate(doc):
        page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(output / f"pdf-{index + 1}.png")
    (output / "qa.json").write_text(
        json.dumps(
            {
                "surfaces": checks,
                "pdf_pages": len(doc),
                "source_result": str(Path(result_path).resolve()),
                "scope": "Real UI with saved result; API responses intercepted locally; no database writes.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"screenshots": str(output), "pdf_pages": len(doc), "checks": checks}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result")
    parser.add_argument("output")
    args = parser.parse_args()
    preview(args.result, args.output)
