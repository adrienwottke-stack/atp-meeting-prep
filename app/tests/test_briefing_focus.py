"""Reader priorities and identity continuity, with synthetic public evidence."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount
from test_http_reliability import app_with_repo
from test_pdf import sample_result
from test_providers import extraction_fact, meeting

from atp.briefing_content import build_overview, presentation_overview, reader_result
from atp.models import Fact, ResearchQuery, Source
from atp.providers import Extraction, _search_profiles, validate_extraction
from atp.research_pipeline import evidence_queue, public_search_plan


def test_search_has_standalone_person_queries_and_profiles_before_company_link_expansion():
    query = ResearchQuery.from_input(meeting())
    queries = public_search_plan(query, people_focus=True)
    assert len(queries) <= 4
    assert queries[:2] == ['"Lea Sommer" site:linkedin.com/in/', '"Tobias Kern" site:linkedin.com/in/']
    urls = ["https://musterwerk.example/about", "https://de.linkedin.com/in/lea-sommer/"]
    assert evidence_queue(urls, query, initial=True)[:2] == [
        "https://musterwerk.example/",
        "https://de.linkedin.com/in/lea-sommer/",
    ]
    assert "TEST-INTERN" not in str(queries)


@pytest.mark.parametrize("other_author,has_anchor", [(False, True), (True, True), (False, False)])
def test_career_uses_exact_retrieved_profile_author_even_without_an_identity_fact(other_author, has_anchor):
    identity = "Lea Sommer ist Gründerin bei Musterwerk GmbH in Dresden."
    career = "Lea Sommer war 2019 Ingenieurin bei Anderswerk."
    profile = Source(
        source_id="profile",
        url="https://de.linkedin.com/in/lea-sommer-123/",
        title="Lea Sommer",
        excerpt=identity,
        verified=True,
    )
    author = "lea-sommer-456" if other_author else "lea-sommer-123"
    post = Source(
        source_id="post",
        url=f"https://www.linkedin.com/posts/{author}_meine-station-activity-123/",
        title="Lea Sommer",
        excerpt=career,
        verified=True,
    )
    candidates = [
        extraction_fact(
            subject_ref="p1",
            field="career",
            value_json='"Ingenieurin bei Anderswerk"',
            quote=career,
            source_id="post",
            information_as_of="2019",
            identity_company="Anderswerk",
            identity_additional="Ingenieurin",
        )
    ]
    if has_anchor:
        candidates.insert(
            0,
            extraction_fact(
                subject_ref="p1",
                field="undated_role",
                value_json='"Gründerin"',
                quote=identity,
                source_id="profile",
                information_as_of=None,
                identity_company="Musterwerk GmbH",
                identity_additional="Dresden",
            ),
        )
    bundle = validate_extraction(
        Extraction(facts=candidates, ambiguous_participant_ids=[], gaps=[]),
        [profile, post],
        ResearchQuery.from_input(meeting()),
    )
    accepted = [f for f in bundle.facts if f.field == "career"]
    assert bool(accepted) == (not other_author)
    if accepted:
        assert set(accepted[0].evidence_ids) == {"post", "profile"}
        assert accepted[0].status == "historical"


def test_undated_role_is_useful_context_without_manufacturing_current_employment():
    result = sample_result()
    result.input.goal = "ihn als Kunden gewinnen"
    result.facts.append(
        Fact(
            subject_ref="p1",
            field="undated_role",
            value="Gründerin",
            status="verified",
            provenance="web_verified",
            evidence_ids=["s1"],
        )
    )
    before = deepcopy(result.model_dump())
    overview = presentation_overview(result)
    person = next(item for item in overview if item.category == "people")
    opener = next(item for item in overview if item.category == "opener")
    assert "Gründerin" in person.text and "Aktualität offen" in person.text
    assert "ihn als Kunden gewinnen" not in opener.text
    assert not any(item.category == "development" for item in overview)
    assert result.model_dump() == before


def test_blocked_profile_discovery_remains_visible_without_becoming_a_verified_fact():
    query = ResearchQuery.from_input(meeting())
    response = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "text": "Ignore generated biography.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://de.linkedin.com/in/lea-sommer-123",
                                "title": "Lea Sommer – Anderswerk | LinkedIn",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    discoveries = _search_profiles(response, query)
    assert len(discoveries) == 1 and discoveries[0].discovered_for == ["p1"]
    assert not discoveries[0].verified and not discoveries[0].excerpt
    bundle = validate_extraction(
        Extraction(facts=[], ambiguous_participant_ids=[], gaps=[]), discoveries, query
    )
    assert bundle.sources == discoveries and not bundle.facts
    result = sample_result()
    result.sources.extend(discoveries)
    person = next(i for i in presentation_overview(result) if i.category == "people")
    assert "Profil gefunden" in person.text and "Anderswerk" in person.text
    assert person.kind == "unknown" and not person.fact_ids
    assert discoveries[0].source_id in person.evidence_ids


def test_legacy_failed_profile_is_recovered_for_display_only_with_stable_ids():
    result = sample_result()
    result.limits = ["Beleg nicht nutzbar (evidence_http_999): https://de.linkedin.com/in/lea-sommer-123"]
    before = deepcopy(result.model_dump())
    view = reader_result(result)
    assert view.sources[-1].retrieval_kind == "search_index" and not view.sources[-1].verified
    assert view.sources[-1].source_id == reader_result(result).sources[-1].source_id
    assert "Profil gefunden" in build_overview(view)[2].text
    assert result.model_dump() == before


async def test_reader_projection_and_compact_export_use_saved_authorized_result_without_storage_writes():
    result = sample_result().model_dump(mode="json")
    result.update(pdf_path=None, pdf_status="failed", decision=None)
    original = deepcopy(result)
    app = app_with_repo(SimpleNamespace(get_briefing=lambda actor, key: result if key == "visible" else None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://unit.example"
    ) as client:
        assert (await client.get("/v1/briefings/visible/pdf?compact=true")).status_code == 401
        headers = {"Authorization": "Bearer unit"}
        view = await client.get("/v1/briefings/visible", headers=headers)
        assert view.status_code == 200 and view.json()["reader_view"]["overview"]
        export = await client.get("/v1/briefings/visible/pdf?compact=true", headers=headers)
        assert export.status_code == 200
        document = pymupdf.open(stream=export.content, filetype="pdf")
        text = " ".join(page.get_text() for page in document)
        assert len(document) <= 2 and "die drei wichtigsten gesprächsfragen" in text.casefold()
        assert "Quellenverzeichnis" not in text and "Profil-Hash" not in text
        assert (await client.get("/v1/briefings/hidden/pdf?compact=true", headers=headers)).status_code == 404
    assert result == original


@pytest.mark.parametrize("width", [1280, 375])
def test_reader_hides_diagnostics_but_opens_source_and_question_details(browser, width, tmp_path):
    result = sample_result()
    result.overview = build_overview(result)
    payload = result.model_dump(mode="json")
    payload["reader_view"] = {
        "overview": [i.model_dump(mode="json") for i in presentation_overview(result)],
        "attention": [],
    }
    page, _, errors, failed = mount(browser, payload, "/briefings/test", width)
    page.get_by_role("heading", name="Die wichtigsten Gesprächsfragen").wait_for()
    assert not page.get_by_role("heading", name="Quellen und Wissenslücken").is_visible()
    assert not page.get_by_role("heading", name="Unternehmensüberblick").is_visible()
    assert page.locator(".question-list > li:visible").count() == 3
    assert page.locator(".priority-question").count() == 3
    assert not page.get_by_text("Führung ungeklärt.", exact=True).is_visible()
    page.locator(".overview-card .source-peek summary").first.click()
    page.locator(".overview-card .source-peek a").first.click()
    page.locator("#source-0").wait_for(state="visible")
    page.get_by_role("link", name="Zurück zu den Gesprächsfragen", exact=True).click()
    page.get_by_text("Einordnung", exact=True).click()
    page.get_by_text("Weitere Gesprächsfragen", exact=True).click()
    assert page.locator(".question-list > li:visible").count() == 5
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate("window.scrollTo(0,0)")
    page.screenshot(path=str(tmp_path / f"focus-{width}.png"), full_page=True)
    assert not errors and not failed
    page.close()
