"""Quota failure persists through the real worker and renders in Chromium."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from test_clarification_lifecycle import CountedProvider
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount
from test_repository import create
from test_repository import database_case as database_case

from atp.providers import ProviderError
from atp.storage import Storage
from atp.workflow import Workflow


@pytest.mark.parametrize("stage", ["assessment", "research", "review", "synthesis"])
def test_quota_failure_at_every_stage_fails_explicitly_instead_of_publishing_empty_report(
    database_case, stage
):
    c = database_case
    c.settings.worker_queue = "quota-regression-" + str(uuid4())
    c.settings.output_dir = c.tmp_path / "pdfs"
    c.data["goal"] = "Konkreten nächsten Schritt zur Zusammenarbeit klären"
    created = create(c)
    provider = CountedProvider(c.settings)

    async def quota(*args, **kwargs):
        budget = args[-1]
        budget.usage.errors.append("provider_insufficient_quota")
        raise ProviderError("provider_insufficient_quota")

    method = {
        "assessment": "assess_input",
        "research": "research",
        "review": "review_evidence",
        "synthesis": "synthesize",
    }[stage]
    setattr(provider, method, quota)
    workflow = Workflow(c.settings, c.repo, Storage(c.settings))
    workflow.provider = provider
    job = c.repo.claim_job("quota-test")
    assert job["run_id"] == created["run_id"]
    asyncio.run(workflow.process(job))
    run = c.repo.get_run(c.actors[0], created["run_id"])
    assert run["status"] == "failed" and run["error"] == "PROVIDER_INSUFFICIENT_QUOTA"
    assert not run["briefing_id"]
    assert "provider_insufficient_quota" in run["usage"]["errors"]
    assert run["input"]["goal"] == c.data["goal"]


@pytest.mark.parametrize("width", [1280, 375])
def test_quota_message_is_readable_and_history_action_works_on_desktop_and_mobile(browser, width):
    page, _, errors, failed = mount(
        browser,
        {
            "status": "failed",
            "phase": "researching",
            "error": "PROVIDER_INSUFFICIENT_QUOTA",
            "version": 2,
            "clarifications": [],
            "usage": {},
        },
        "/runs/quota-test",
        width,
    )
    page.get_by_role("heading", name="Die Vorbereitung wurde gestoppt.").wait_for()
    notice = page.get_by_text("Die Recherche wurde gestoppt, weil das API-Guthaben", exact=False)
    notice.wait_for()
    assert "Abrechnungslimit" in notice.inner_text()
    assert "gespeicherte Stand bleibt erhalten" in notice.inner_text()
    assert not page.get_by_text("Recherche läuft", exact=True).count()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    proof = Path("output/person-research/visual")
    proof.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(proof / f"quota-{width}.png"), full_page=True)
    action = page.get_by_role("link", name="Zur Historie", exact=True)
    assert action.bounding_box()["height"] >= 44
    action.click()
    page.wait_for_url("**/history")
    assert not errors and not failed
    page.close()
