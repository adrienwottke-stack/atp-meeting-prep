"""Explicit hosted smoke test with public inputs and cleanup of its own meeting.

Never prints account data, cookies, prompts or internal configuration. Run only
after the release coordinator has completed the deployment.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

PUBLIC_INPUT = {
    "company": {"name": "TRUMPF SE + Co. KG", "website": "https://www.trumpf.com"},
    "people": [{"name": "Nicola Leibinger-Kammüller"}, {"name": "Stephan Mayer"}],
    "meeting_type": "setup",
    "relationship": "new_contact",
    "goal": "In einem ersten Kennenlerngespräch das Geschäftsmodell verstehen, öffentlich belegte KI- und Digitalisierungsthemen einordnen und gemeinsam einen sinnvollen nächsten Prüfschritt bestimmen.",
    "internal_context": "",
    "confirmed": True,
    "public_identifiers_confirmed": True,
}
POLL_STATES = {
    "queued",
    "validating",
    "researching",
    "evaluating",
    "generating",
    "exporting",
    "needs_clarification",
    "completed",
    "partial",
    "failed",
    "cancelled",
}


def safe_state(value):
    return value if isinstance(value, str) and value in POLL_STATES else "unknown"


def numeric_usage(run):
    usage = run.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    allowed = {
        "active_seconds",
        "model_requests",
        "search_calls",
        "observed_search_calls",
        "search_calls_unknown_attempts",
        "evidence_fetches",
        "pdf_fetches",
        "input_tokens",
        "output_tokens",
    }
    return {
        key: value
        for key, value in usage.items()
        if key in allowed and type(value) in (int, float) and math.isfinite(value) and value >= 0
    }


class SmokeFailure(Exception):
    """Contains only a static check name, never a response body."""


def require(value, label):
    if not value:
        raise SmokeFailure(label)


def checked_base(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("A plain HTTPS application origin is required")
    return value.rstrip("/")


def unknown_answers(questions):
    """Only explicitly permitted unknowns; never infer or answer missing inputs."""
    result = {}
    for question in questions:
        if question.get("answer"):
            continue
        if (
            question.get("code") == "K01"
            or question.get("allow_unknown") is False
            or "leave_unknown" not in question.get("answer_types", ["provide", "select", "leave_unknown"])
        ):
            continue
        result[question["question_id"]] = {"action": "leave_unknown", "value": ""}
    return result


def validate_result(result):
    require(result.get("status") in {"completed", "partial"}, "briefing_terminal_status")
    require(result.get("fixture_mode") is False, "briefing_real_provider")
    require(
        result.get("quality", {}).get("state") in {"sufficient", "limited", "clarification_required"},
        "quality_assessed",
    )
    overview = result.get("overview", [])
    require(0 < len(overview) <= 5, "overview_present")
    require(len({item["category"] for item in overview}) == len(overview), "overview_unique_categories")
    source_ids = {source["source_id"] for source in result.get("sources", [])}
    facts = {fact["fact_id"]: fact for fact in result.get("facts", [])}
    for fact in facts.values():
        require(set(fact.get("evidence_ids", [])) <= source_ids, "fact_source_refs")
    claims = list(result.get("company", [])) + list(overview)
    for person in result.get("people", []):
        claims.extend([person["role"], *person.get("career", []), *person.get("topics", [])])
    for claim in claims:
        refs = claim.get("fact_ids", [])
        require(set(refs) <= facts.keys(), "claim_fact_refs")
        require(set(claim.get("evidence_ids", [])) <= source_ids, "claim_source_refs")
        if claim.get("kind") == "fact":
            require(bool(refs or claim.get("evidence_ids")), "fact_claim_attributable")
            require(
                all(facts[id]["status"] in {"verified", "historical"} for id in refs), "fact_claim_supported"
            )
    for item in [*result.get("opportunities", []), *result.get("icp", {}).get("criteria", [])]:
        require(set(item.get("evidence_ids", [])) <= source_ids, "detail_source_refs")


def browser_proof(client, base, briefing_id, output):
    from playwright.sync_api import sync_playwright

    errors, failed, responses = [], [], []
    secrets = [cookie.value for cookie in client.cookies.jar if cookie.value]
    diagnostic = {
        "status": "running",
        "stage": "initialization",
        "responses": responses,
        "page_errors": errors,
        "failed_requests": failed,
        "completed_widths": [],
    }

    def safe_text(value):
        text = str(value)
        for secret in secrets:
            text = text.replace(secret, "<redacted>")
        text = re.sub(r"https?://\S+", "<url>", text)
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>", text)
        text = re.sub(r"(?:sb_secret_|sk-proj-)[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_.-]{30,}", "<token>", text)
        return text[:400]

    def safe_path(url):
        path = urlsplit(url).path
        if not path.startswith(("/static/", "/v1/", "/briefings/")) and path not in {
            "/",
            "/history",
            "/login",
            "/help",
        }:
            return "<other>"
        return re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}", "<id>", path)[:200]

    def observed_response(response):
        if len(responses) < 100:
            responses.append(
                {
                    "path": safe_path(response.url),
                    "status": response.status,
                    "resource_type": response.request.resource_type,
                    "mime": response.headers.get("content-type", "")[:100],
                }
            )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = None
        try:
            context.add_cookies(
                [
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "url": base,
                        "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
                        "secure": cookie.secure,
                        "sameSite": "Strict",
                    }
                    for cookie in client.cookies.jar
                ]
            )
            page = context.new_page()
            page.set_default_timeout(5000)
            page.on("pageerror", lambda error: errors.append(safe_text(error)) if len(errors) < 30 else None)
            page.on(
                "console",
                lambda message: (
                    errors.append(safe_text(message.text))
                    if message.type == "error" and len(errors) < 30
                    else None
                ),
            )
            page.on(
                "requestfailed",
                lambda request: (
                    failed.append({"path": safe_path(request.url), "error": safe_text(request.failure)})
                    if len(failed) < 30
                    else None
                ),
            )
            page.on("response", observed_response)
            for width, height in [(1280, 800), (375, 812)]:
                diagnostic.update(width=width, stage="viewport")
                page.set_viewport_size({"width": width, "height": height})
                if not diagnostic["completed_widths"]:
                    diagnostic["stage"] = "goto"
                    page.goto(
                        base + "/briefings/" + quote(briefing_id, safe=""),
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                diagnostic["stage"] = "cards"
                page.locator(".overview-card").first.wait_for(timeout=30000)
                page.evaluate("document.fonts.ready")
                diagnostic["stage"] = "layout"
                require(
                    page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"),
                    "browser_no_horizontal_overflow",
                )
                more_actions = page.locator("summary", has_text="Weitere Aktionen")
                if more_actions.count() and not more_actions.locator("..").evaluate("node => node.open"):
                    more_actions.click()
                require(
                    page.get_by_role("link", name="HTML herunterladen").count() == 1,
                    "browser_html_download_available",
                )
                diagnostic["stage"] = "screenshot"
                page.screenshot(path=str(output / f"desktop-{width}.png"), full_page=True)
                diagnostic["completed_widths"].append(width)
            diagnostic["stage"] = "browser_errors"
            require(not errors, "browser_no_console_errors")
            require(not failed, "browser_no_failed_requests")
            diagnostic.update(status="passed", stage="complete")
        except Exception as exc:
            diagnostic.update(status="failed", error_type=type(exc).__name__)
            if page is not None:
                try:
                    diagnostic["page_path"] = safe_path(page.url)
                    diagnostic["overview_cards"] = page.locator(".overview-card").count()
                    diagnostic["alerts"] = [
                        safe_text(text)
                        for text in page.locator('.notice.error, [role="alert"]').all_inner_texts()[:5]
                    ]
                except Exception as capture:
                    diagnostic["dom_capture_error"] = type(capture).__name__
                try:
                    page.screenshot(path=str(output / "browser-failure.png"), full_page=True, timeout=5000)
                    diagnostic["failure_screenshot"] = "browser-failure.png"
                except Exception as capture:
                    diagnostic["screenshot_error"] = type(capture).__name__
            raise
        finally:
            (output / "browser-diagnostics.json").write_text(
                json.dumps(diagnostic, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            context.close()
            browser.close()
    return {"widths": [1280, 375], "console_errors": len(errors), "failed_requests": len(failed)}


def run_smoke(
    base, account, output, *, client=None, browser=True, now=time.monotonic, sleep=time.sleep, max_wait=650
):
    base = checked_base(base)
    require(0 < max_wait <= 1300, "bounded_poll_window")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    own_client = client is None
    client = client or httpx.Client(base_url=base, headers={"Origin": base}, timeout=30, trust_env=False)
    client.headers["Origin"] = base
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "base_url": base,
        "checks": [],
        "status": "failed",
        "clarification_rounds": 0,
        "max_wait_seconds": max_wait,
    }
    meeting_id = None
    logged_in = False

    def check(value, label):
        require(value, label)
        if label not in report["checks"]:
            report["checks"].append(label)

    def request_id(response):
        header = response.headers.get("x-request-id", "")
        check(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", header)), "request_id_header")
        check(response.json().get("request_id") == header, "request_id_body_matches")

    try:
        response = client.get("/health", timeout=30)
        check(
            response.status_code == 200
            and response.json().get("status") == "ok"
            and response.json().get("fixture_mode") is False,
            "https_real_health",
        )
        response = client.get("/v1/meetings", timeout=30)
        check(response.status_code == 401, "unauthenticated_denied")
        request_id(response)
        response = client.post(
            "/v1/auth/login", json={key: account[key] for key in ["email", "password"]}, timeout=30
        )
        check(response.status_code == 200, "administrator_login")
        logged_in = True
        cookie = next((cookie for cookie in client.cookies.jar if cookie.name == "atp_access"), None)
        check(
            cookie is not None and cookie.secure and cookie.has_nonstandard_attr("HttpOnly"),
            "secure_httponly_cookie",
        )
        response = client.get("/v1/auth/me", timeout=30)
        check(response.status_code == 200 and response.json().get("role") == "admin", "administrator_role")
        response = client.post(
            "/v1/preparations", json={}, headers={"Origin": "https://rejected.invalid"}, timeout=30
        )
        check(response.status_code == 403, "cross_origin_write_denied")
        request_id(response)
        response = client.get("/help", timeout=30)
        check(
            response.status_code == 200 and "text/html" in response.headers.get("content-type", ""),
            "authenticated_help",
        )
        response = client.get("/v1/handbook/pdf", timeout=30)
        check(
            response.status_code == 200 and response.content.startswith(b"%PDF-"),
            "administrator_handbook_pdf",
        )
        (output / "handbook.pdf").write_bytes(response.content)
        response = client.post(
            "/v1/preparations", json=PUBLIC_INPUT, headers={"Idempotency-Key": str(uuid4())}, timeout=30
        )
        check(response.status_code == 202, "public_preparation_created")
        ids = response.json()
        meeting_id = ids["meeting_id"]
        run_id = ids["run_id"]
        report.update(meeting_id=meeting_id, run_id=run_id)
        started = now()
        deadline = started + max_wait
        answered = set()
        run = {}
        previous_status = None
        previous_poll_at = started
        queued_seconds = 0
        last_progress_at = float("-inf")
        last_progress_state = None
        while now() < deadline:
            response = client.get(
                "/v1/runs/" + quote(run_id, safe=""), timeout=min(30, max(0.1, deadline - now()))
            )
            check(response.status_code == 200, "real_worker_polled")
            run = response.json()
            polled_at = now()
            if previous_status == "queued":
                queued_seconds += max(0, polled_at - previous_poll_at)
            previous_poll_at = polled_at
            previous_status = safe_state(run.get("status"))
            report.update(
                run_status=previous_status,
                seconds=round(polled_at - started, 2),
                observed_queued_seconds=round(queued_seconds, 2),
            )
            progress_state = (previous_status, safe_state(run.get("phase")), report["clarification_rounds"])
            if progress_state != last_progress_state or polled_at - last_progress_at >= 15:
                progress = {
                    "event": "progress",
                    "status": previous_status,
                    "phase": safe_state(run.get("phase", run.get("status"))),
                    "wall_seconds": report["seconds"],
                    "max_wait_seconds": max_wait,
                    "observed_queued_seconds": report["observed_queued_seconds"],
                    "clarification_rounds": report["clarification_rounds"],
                    **numeric_usage(run),
                }
                (output / "progress.json").write_text(json.dumps(progress, indent=2), encoding="utf-8")
                print(json.dumps(progress), flush=True)
                last_progress_state = progress_state
                last_progress_at = polled_at
            if run.get("briefing_id") or run.get("status") in {"failed", "cancelled"}:
                break
            if run.get("status") == "needs_clarification":
                signature = (
                    run["version"],
                    tuple(question["question_id"] for question in run.get("clarifications", [])),
                )
                if signature not in answered:
                    check(report["clarification_rounds"] < 2, "clarification_round_limit")
                    answers = unknown_answers(run.get("clarifications", []))
                    check(bool(answers), "clarification_can_be_left_unknown")
                    response = client.post(
                        "/v1/runs/" + quote(run_id, safe="") + "/answers",
                        json={"expected_version": run["version"], "answers": answers},
                        timeout=min(30, max(0.1, deadline - now())),
                    )
                    check(response.status_code == 200, "structured_unknown_answers_accepted")
                    report["clarification_rounds"] += 1
                    answered.add(signature)
            sleep(min(3, max(0, deadline - now())))
        report.update(run_status=safe_state(run.get("status")), seconds=round(now() - started, 2))
        check(bool(run.get("briefing_id")), "real_worker_completed_with_briefing")
        briefing_id = run["briefing_id"]
        report["briefing_id"] = briefing_id
        response = client.get("/v1/briefings/" + quote(briefing_id, safe=""), timeout=30)
        check(response.status_code == 200, "briefing_download")
        result = response.json()
        (output / "briefing.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        validate_result(result)
        report.update(
            quality=result["quality"]["state"],
            facts=len(result.get("facts", [])),
            sources=len(result.get("sources", [])),
            overview_categories=[item["category"] for item in result["overview"]],
            usage=numeric_usage(run),
        )
        for extension, prefix in [("pdf", b"%PDF-"), ("html", b"<!doctype html")]:
            response = client.get(
                "/v1/briefings/" + quote(briefing_id, safe="") + "/" + extension, timeout=30
            )
            check(
                response.status_code == 200 and response.content.lower().startswith(prefix.lower()),
                extension + "_download",
            )
            if extension == "html":
                check(
                    "default-src 'none'" in response.headers.get("content-security-policy", ""),
                    "html_restrictive_csp",
                )
                check("<script" not in response.text.lower(), "html_no_scripts")
            (output / ("briefing." + extension)).write_bytes(response.content)
        if browser:
            report["browser"] = browser_proof(client, base, briefing_id, output)
        report["status"] = "passed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        if isinstance(exc, SmokeFailure):
            report["failed_check"] = str(exc)
    finally:
        if meeting_id:
            try:
                response = client.delete("/v1/meetings/" + quote(meeting_id, safe=""), timeout=30)
                report["cleanup_delete_http"] = response.status_code
                if response.status_code != 202:
                    report["status"] = "failed"
            except Exception as exc:
                report.update(status="failed", cleanup_error_type=type(exc).__name__)
        if logged_in:
            try:
                report["logout_http"] = client.post("/v1/auth/logout", timeout=30).status_code
                report["logged_out_denied"] = client.get("/v1/meetings", timeout=30).status_code == 401
                if report["logout_http"] != 200 or not report["logged_out_denied"]:
                    report["status"] = "failed"
            except Exception as exc:
                report.update(status="failed", logout_error_type=type(exc).__name__)
        report["finished_at"] = datetime.now(UTC).isoformat()
        (output / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if own_client:
            client.close()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--account-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--max-wait",
        type=int,
        default=650,
        help="Wall-clock poll limit including queue waits (1–1300 seconds); does not change the server's active processing budget.",
    )
    args = parser.parse_args(argv)
    try:
        account = json.loads(args.account_file.read_text(encoding="utf-8-sig"))
        report = run_smoke(
            args.base, account, args.output, browser=not args.no_browser, max_wait=args.max_wait
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1
    print(
        json.dumps(
            {
                key: report[key]
                for key in ["status", "run_status", "seconds", "failed_check", "error_type"]
                if key in report
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
