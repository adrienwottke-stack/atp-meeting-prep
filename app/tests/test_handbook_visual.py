"""Real browser checks for the screenshot guide, without a live service or account."""

from pathlib import Path

import pytest
import test_clarification_presentation as presentation

from atp.handbook import render_handbook

ROOT = Path(__file__).resolve().parents[1]
browser = presentation.browser


@pytest.mark.parametrize("width", [1280, 375])
def test_guide_images_zoom_navigation_and_mobile_layout(browser, width):
    html = render_handbook(ROOT / "docs/handbook", operator=True, memory_enabled=True)
    page, _, errors, failed = presentation.mount(browser, {}, "/help", width, responses={"/help": html})
    page.get_by_role("heading", level=1).wait_for()
    assert page.locator(".guide-step").count() == 7
    assert page.locator(".guide-reference details[open]").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for image in page.locator(".guide-shot img").all():
        parent = image.locator("xpath=ancestor::details")
        if parent.count():
            parent.locator("summary").first.click()
        image.scroll_into_view_if_needed()
        image.evaluate("e => e.loading = 'eager'")
        page.wait_for_function(
            "src => [...document.images].find(i => i.src === src)?.naturalWidth > 0",
            arg=image.get_attribute("src") and image.evaluate("e => e.src"),
        )
    page.locator(".guide-image-link").first.click()
    assert page.get_by_role("dialog").is_visible()
    assert page.locator(".guide-lightbox .guide-pin").count() == 2
    if width == 375:
        assert page.locator(".guide-lightbox-stage").evaluate("e => e.scrollWidth > e.clientWidth")
        # The new centered form moves the first marker left. Check that the
        # enlarged image exposes that marker, independent of its old offset.
        stage = page.locator(".guide-lightbox-stage").bounding_box()
        pin = page.locator(".guide-lightbox .guide-pin").first.bounding_box()
        assert stage["x"] <= pin["x"]
        assert pin["x"] + pin["width"] <= stage["x"] + stage["width"]
    page.keyboard.press("Escape")
    assert not page.get_by_role("dialog").is_visible()
    assert page.locator(".guide-image-link").first.evaluate("e => e === document.activeElement")
    page.get_by_role("link", name="Kurze Antworten", exact=True).click()
    assert page.url.endswith("#fragen")
    page.get_by_text("Das PDF ist noch nicht verfügbar.", exact=True).click()
    assert page.locator("#fragen details[open]").count() == 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    output = ROOT / "output/handbook-visual"
    output.mkdir(parents=True, exist_ok=True)
    page.keyboard.press("Home")
    page.evaluate("scrollTo(0, 0)")
    page.screenshot(path=str(output / f"hilfe-{width}.png"), full_page=True)
    page.screenshot(path=str(output / f"hilfe-einstieg-{width}.png"))
    page.locator("#starten").scroll_into_view_if_needed()
    page.screenshot(path=str(output / f"hilfe-schritt-{width}.png"))
    assert not errors and not failed, (errors, failed)
    page.close()


def test_member_reference_and_feature_visibility():
    html = render_handbook(ROOT / "docs/handbook")
    assert "Projektbriefing erstellen" not in html
    assert 'id="wissen"' not in html
    assert "Betrieb: Aufgaben und Wiederherstellung" not in html
    assert 'href="/v1/handbook/pdf"' not in html
    assert len(list((ROOT / "src/atp/static/handbook").glob("*.webp"))) == 8
