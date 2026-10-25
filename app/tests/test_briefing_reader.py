"""Reading, evidence access and optional editing on desktop/tablet/mobile."""

import pymupdf
import pytest
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount
from test_pdf import sample_result

from atp.briefing_content import presentation_overview
from atp.pdf import render_pdf


@pytest.mark.parametrize("width", [1280, 820, 375])
def test_reading_path_and_expanded_actions_do_not_cover_content(browser, width):
    result = sample_result()
    result.input.goal = "Kunden gewinnen; nur interne Zielsetzung"
    before = result.model_dump(mode="json")
    payload = dict(before)
    overview = presentation_overview(result)
    payload["reader_view"] = {"overview": [item.model_dump(mode="json") for item in overview]}
    page, _, errors, failed = mount(browser, payload, "/briefings/test", width)
    page.locator(".reader-opener").wait_for()
    assert result.questions[0].text not in page.locator(".reader-opener").inner_text()
    assert result.input.goal not in page.locator(".reader-opener").inner_text()
    assert page.locator(".question-list > li:visible").count() == 3
    assert not page.get_by_label("Deine Entscheidung", exact=True).is_visible()
    page.get_by_text("Weitere Aktionen", exact=True).click()
    actions = page.locator(".briefing-tools").bounding_box()
    purpose = page.locator(".briefing-purpose").bounding_box()
    assert actions["y"] + actions["height"] <= purpose["y"]
    assert page.get_by_role("link", name="HTML herunterladen", exact=True).is_visible()
    assert page.get_by_role("link", name="PDF mit Nachweisen", exact=True).is_visible()
    page.get_by_text("Entscheidung festhalten", exact=True).click()
    assert page.get_by_label("Deine Entscheidung", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors and not failed
    assert result.model_dump(mode="json") == before
    page.close()


def test_compact_pdf_keeps_real_content_readable_without_forcing_a_single_page():
    result = sample_result(long=True)
    result.input.goal = "Ausgangslage und nächste Schritte besprechen. " * 20
    before = result.model_dump(mode="json")
    doc = pymupdf.open(stream=render_pdf(result, compact=True), filetype="pdf")
    text = " ".join(page.get_text() for page in doc)
    assert len(doc) >= 2
    assert result.questions[2].text in text
    for page in doc:
        for x0, y0, x1, y1, *_ in page.get_text("words"):
            assert 39 <= x0 < x1 <= page.rect.width - 39
            assert 15 <= y0 < y1 <= page.rect.height - 15
    assert result.model_dump(mode="json") == before
