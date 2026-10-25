"""Portable, deterministic HTML rendering; all data is synthetic."""

from html.parser import HTMLParser

import pymupdf
import pytest
from test_clarification_presentation import browser as browser
from test_pdf import sample_result

from atp.briefing_content import build_overview
from atp.html_export import HTML_CSP, render_html
from atp.models import QualityAssessment


class ParsedHTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_html_content_determinism_escaping_and_references():
    result = sample_result()
    result.overview = build_overview(result)
    result.quality = QualityAssessment(state="limited", reasons=["Rolle nicht bestätigt."])
    result.resolutions = {
        "goal": {
            "question": "Welches Ziel?",
            "action": "provide",
            "value": "Prioritäten abstimmen",
            "confirmed_by": "test-user",
            "confirmed_at": "2026-10-15T10:00:00Z",
        }
    }
    before = result.model_dump(mode="json")
    html = render_html(result)
    assert html == render_html(result) == render_html(before)
    assert result.model_dump(mode="json") == before
    parsed = ParsedHTML(html)
    text = " ".join(parsed.text)
    for content in [
        result.input.company.name,
        "Lea Sommer",
        "Tobias Groß",
        "TESTMODUS",
        "Unternehmen in 20 Sekunden",
        "Dein Gesprächseinstieg",
        "Mit Einschränkungen verwendbar",
        "Rolle nicht bestätigt.",
        "Bestätigte Nutzereingabe; kein Web-Beleg",
        "test-user",
        "Erstellung abgeschlossen",
        "Quellenverzeichnis",
        "Informationsstand",
        "Menschliche Entscheidung",
        '<script>alert("x")</script> & <b>kein HTML</b>',
    ]:
        assert content in text
    assert not any(tag in {"script", "iframe", "object", "embed", "link"} for tag, _ in parsed.tags)
    assert not any(key.startswith("on") for _, attrs in parsed.tags for key in attrs)
    images = [attrs for tag, attrs in parsed.tags if tag == "img"]
    assert len(images) == 1 and images[0]["src"].startswith("data:image/png;base64,")
    assert "default-src 'none'" in HTML_CSP
    assert any(attrs.get("http-equiv") == "Content-Security-Policy" for _, attrs in parsed.tags)
    assert any(attrs.get("href") == result.sources[0].url for _, attrs in parsed.tags)
    ids = {attrs["id"] for _, attrs in parsed.tags if "id" in attrs}
    assert {"A01", "A02", "A03", "A04", "A05", "icp", "A06", "decision", "source-1"} <= ids
    assert all(
        attrs["href"][1:] in ids
        for tag, attrs in parsed.tags
        if tag == "a" and attrs.get("href", "").startswith("#")
    )
    assert text.index("Gesprächs-Storyline") < text.index(
        "Zielgruppenpassung", text.index("Gesprächs-Storyline")
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "https://user:password@example.org/a",
        "https://example.org/\nwrong",
        "https://example.org\\@bad.invalid",
        "file:///private.txt",
    ],
)
def test_html_unsafe_sources_are_visible_text_without_active_links(url):
    result = sample_result()
    object.__setattr__(result.sources[0], "url", url)
    parsed = ParsedHTML(render_html(result))
    assert not any(attrs.get("href") == url for tag, attrs in parsed.tags if tag == "a")
    assert url in " ".join(parsed.text)


def test_html_legacy_partial_result_and_quality_default():
    result = sample_result()
    result.status = "partial"
    html = render_html(result)
    assert "Teilergebnis" in html and "Fachliche Verwendbarkeit nicht bewertet" in html
    assert "Briefing auf einen Blick" in html and "Einordnung und nächster Schritt" in html
    assert '<article class="card">' in html  # Legacy results also receive a reader projection.


@pytest.mark.parametrize("width", [1280, 375])
def test_export_opens_offline_without_resources_and_prints(browser, width, tmp_path):
    result = sample_result(long=True)
    result.input.company.name = "Müller Präzisionstechnik " + "LangerName" * 35
    result.overview = build_overview(result)
    path = tmp_path / "briefing.html"
    path.write_text(render_html(result), encoding="utf-8")
    context = browser.new_context(
        viewport={"width": width, "height": 812 if width == 375 else 800}, offline=True
    )
    page = context.new_page()
    network, errors = [], []
    page.on(
        "request",
        lambda request: network.append(request.url) if request.url.startswith(("https:", "http:")) else None,
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(path.as_uri())
    assert page.get_by_role("heading", name="Unternehmen in 20 Sekunden").is_visible()
    assert page.locator(".card").count() == 4
    assert page.locator(".priority").count() == 3
    assert page.locator("img").evaluate("img => img.complete && img.naturalWidth > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_text("Unternehmen & Personen", exact=True).click()
    assert page.locator("#A02").is_visible()
    page.evaluate("window.scrollTo(0,0)")
    page.screenshot(path=str(tmp_path / f"offline-viewport-{width}.png"))
    page.screenshot(path=str(tmp_path / f"offline-full-{width}.png"), full_page=True)
    page.emulate_media(media="print")
    assert not page.locator("nav").is_visible()
    printed = page.pdf(format="A4", print_background=True)
    doc = pymupdf.open(stream=printed, filetype="pdf")
    assert 1 < len(doc) < 40
    assert "Quellenverzeichnis" in "".join(p.get_text() for p in doc)
    for printed_page in doc:
        assert all(
            10 <= word[0] < word[2] <= printed_page.rect.width - 10 for word in printed_page.get_text("words")
        )
    (tmp_path / f"offline-print-{width}.pdf").write_bytes(printed)
    assert not network and not errors
    context.close()
