"""Offline wave, resume, PDF and passage acceptance tests with real hard budgets."""

import asyncio
import copy
import json
from types import SimpleNamespace

import fitz
import pytest
from test_evidence import Response as EvidenceResponse
from test_evidence import factory_for
from test_providers import Responses, extraction_fact, meeting, settings, source

from atp.budget import Budget
from atp.config import Settings
from atp.evidence import (
    MAX_PDF_BYTES,
    EvidenceError,
    EvidenceFetcher,
    company_links,
    page_text,
    pdf_text,
)
from atp.models import ResearchQuery, Source
from atp.passages import select_passages
from atp.providers import Extraction, Provider, _rank_urls, _value_supported, validate_extraction
from atp.research_checks import boolean_verdict


def real_settings():
    return Settings(
        _env_file=None,
        max_model_requests=16,
        max_search_calls=24,
        max_evidence_fetches=40,
        deadline_seconds=480,
        research_cutoff_seconds=390,
    )


class WaveProvider(Provider):
    def __init__(self, *, complete=False):
        self.events = []
        self.complete = complete
        super().__init__(real_settings(), client=SimpleNamespace(), fetcher=self)

    async def fetch(self, url, budget):
        budget.reserve(evidence=1)
        return Source(
            source_id=url,
            url=url,
            title="Public company",
            excerpt=(
                "Musterwerk GmbH bietet Anlagen und Fertigung. Industrielle Kunden. "
                "Musterwerk GmbH ist familiengeführt mit 120 Mitarbeitenden. Kein Konzern und keine Behörde. "
                "Musterwerk GmbH startete 2026 ein KI-Projekt."
            ),
            verified=True,
        )

    async def _request(self, budget, **kwargs):
        assert kwargs.get("search") and "TEST-INTERN-4711" not in json.dumps(kwargs["data"])
        budget.reserve(model=1, searches=4)
        self.events.append(kwargs["data"])
        index = len(self.events)
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "sources": [{"url": f"https://press.example.org/wave-{index}"}],
                    },
                }
            ]
        }

    async def _extract_sources(self, query, sources, budget):
        budget.reserve(model=1)
        stage = len(self.events)
        src = sources[0]
        fields = {
            1: [("business_model", "Anlagen")],
            2: [("services", "Fertigung")],
            3: [("business_development", "KI-Projekt")],
        }.get(stage, [])
        if self.complete:
            fields = [
                ("business_model", "Anlagen"),
                ("services", "Fertigung"),
                ("business_development", "KI-Projekt"),
                ("employees_total", 120),
                ("owner_or_family_led", True),
                ("is_corporation", False),
                ("is_public_authority", False),
            ]
        facts = [
            extraction_fact(
                field=field, value_json=json.dumps(value), source_id=src.source_id, quote=src.excerpt
            )
            for field, value in fields
        ]
        return Extraction(facts=facts, ambiguous_participant_ids=[], gaps=[])


async def test_three_bounded_waves_merge_sources_and_preserve_fact_ids():
    provider, budget, states = WaveProvider(), Budget(real_settings()), []
    result = await provider.research(
        ResearchQuery.from_input(meeting(people=[])),
        budget,
        persist=lambda state: states.append(copy.deepcopy(state)),
    )
    assert len(provider.events) == 3 and len(states) > 3
    states = [state for state in states if state.get("active_wave") is None]
    assert [s["completed_waves"] for s in states] == [1, 2, 3]
    ids = [{f["field"]: f["fact_id"] for f in s["bundle"]["facts"]} for s in states]
    assert ids[0]["business_model"] == ids[1]["business_model"] == ids[2]["business_model"]
    assert {f.field for f in result.facts} == {"business_model", "services", "business_development"}
    assert budget.usage.model_requests == 6 and budget.usage.search_calls == 12
    assert len(result.sources) == 3
    assert len(result.research_coverage) == 4
    assert "company" == provider.events[1]["missing_areas"][0]
    assert states[-1]["usage"]["model_requests"] == 6


async def test_complete_four_area_evidence_stops_after_first_wave():
    provider, budget = WaveProvider(complete=True), Budget(real_settings())
    result = await provider.research(ResearchQuery.from_input(meeting(people=[])), budget)
    assert len(provider.events) == 1
    assert all(area.status == "covered" for area in result.research_coverage)
    assert not result.supplemental_search_performed


async def test_resume_uses_completed_wave_without_repeating_search_or_reassigning_ids():
    provider, states, query = WaveProvider(), [], ResearchQuery.from_input(meeting(people=[]))

    def interrupted_save(state):
        if state.get("active_wave") is not None:
            return
        states.append(copy.deepcopy(state))
        raise RuntimeError("controlled worker restart")

    with pytest.raises(RuntimeError, match="restart"):
        await provider.research(query, Budget(real_settings()), persist=interrupted_save)
    first = states[0]
    budget = Budget(real_settings(), first["usage"])
    result = await provider.research(query, budget, checkpoint=first)
    assert len(provider.events) == 3
    assert result.facts[0].fact_id == first["bundle"]["facts"][0]["fact_id"]
    assert budget.usage.model_requests == 6
    assert budget.usage.evidence_fetches == 4  # root + one source per wave


async def test_research_cutoff_preserves_review_and_export_window_without_calls():
    provider = WaveProvider()
    budget = Budget(real_settings(), {"active_seconds": 391})
    result = await provider.research(ResearchQuery.from_input(meeting()), budget)
    assert not provider.events and result.limits
    assert budget.remaining_seconds > 80


async def test_inflight_research_is_cancelled_at_window_instead_of_using_export_time(monkeypatch):
    checks, cancelled = [], []

    def window(_budget):
        checks.append(True)
        return 6 if len(checks) == 1 else 0.02

    monkeypatch.setattr("atp.research_pipeline._research_seconds", window)

    class SlowProvider(WaveProvider):
        async def _request(self, budget, **kwargs):
            budget.reserve(model=1, searches=4)
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.append(True)

    budget = Budget(real_settings())
    result = await SlowProvider().research(ResearchQuery.from_input(meeting()), budget)
    assert cancelled and budget.elapsed < 0.5
    assert budget.usage.model_requests == 1 and budget.usage.search_calls == 4
    assert result.limits and budget.remaining_seconds > 479


async def test_user_public_url_hints_fetch_extract_without_search():
    provider, budget = WaveProvider(), Budget(real_settings())
    provider.events = ["stage-one"]  # controlled extraction selector
    result = await provider.research_evidence_urls(
        ResearchQuery.from_input(meeting()), ["https://press.example.org/news"], budget
    )
    assert result.facts and len(provider.events) == 1
    assert budget.usage.search_calls == 0 and budget.usage.model_requests == 1


async def test_public_url_hints_keep_ssrf_redirect_protection():
    calls = []
    provider = Provider(
        settings(),
        client=SimpleNamespace(),
        fetcher=EvidenceFetcher(
            resolver=lambda h, p: ["93.184.216.34"],
            connection_factory=factory_for(
                [EvidenceResponse(302, {"Location": "http://169.254.169.254/"})], calls
            ),
        ),
    )
    result = await provider.research_evidence_urls(
        ResearchQuery.from_input(meeting()), ["https://example.com/about"], Budget(real_settings())
    )
    assert not result.facts and len(calls) == 1
    assert any("blocked" in limit for limit in result.limits)


async def test_extraction_includes_ninth_source_and_relevant_passage_after_7000_characters():
    responses = Responses()
    responses.response.output_parsed = Extraction(facts=[], ambiguous_participant_ids=[], gaps=[])
    provider = Provider(settings(), client=SimpleNamespace(responses=responses))
    sources = [source().model_copy(update={"source_id": f"s-{i}"}) for i in range(9)]
    sources[8].excerpt = "Neutral company context. " * 330 + "Musterwerk GmbH: wichtiges KI-Projekt 2026."
    await provider._extract_sources(ResearchQuery.from_input(meeting()), sources, Budget(real_settings()))
    data = json.loads(responses.calls[0]["input"])
    assert len(data["untrusted_sources"]) == 9
    assert "wichtiges KI-Projekt 2026" in data["untrusted_sources"][8]["excerpt"]


def test_nav_cookie_footer_are_removed_before_late_passage_selection():
    html = (
        "<nav>" + "navigation " * 2500 + "</nav><div class='cookie-banner'>cookie text</div>"
        "<main><h1>Unternehmen</h1><p>Musterwerk entwickelt wichtige Anlagen.</p></main>"
        "<footer>boilerplate footer</footer>"
    ).encode()
    text, _ = page_text(html, "text/html")
    assert "Musterwerk" in text
    assert not any(term in text for term in ("navigation", "cookie text", "boilerplate"))
    long_text = "Neutral context. " * 1500 + "Musterwerk GmbH startete 2026 ein wichtiges KI-Projekt."
    assert "wichtiges KI-Projekt" in select_passages(long_text)


def test_relevant_pdf_links_allowed_but_catalog_and_image_assets_skipped():
    urls = [
        "https://example.com/annual-report.pdf",
        "https://example.com/catalog.pdf",
        "https://example.com/a.png",
    ]
    assert _rank_urls(urls, "example.com") == [urls[0]]
    assert company_links(b'<a href="/company-report.pdf">report</a>', "https://example.com/") == [
        "https://example.com/company-report.pdf"
    ]


@pytest.mark.parametrize(
    "value,quote,verdict",
    [
        (True, "Das Unternehmen ist keine Tochtergesellschaft.", "contradicted"),
        (False, "Das Unternehmen ist keine Tochtergesellschaft.", "unclear"),
        (False, "Das Unternehmen gehört keinem Konzern an.", "supported"),
        (True, "Kein Konzern. Allerdings ist die Firma eine Tochtergesellschaft.", "unclear"),
    ],
)
def test_corporation_guard_distinguishes_group_from_subsidiary_denial(value, quote, verdict):
    assert boolean_verdict("is_corporation", value, quote)[0] == verdict


@pytest.mark.parametrize(
    "quote,expected",
    [
        ("The company is not family-led and owner-managed.", "supported"),
        ("The company is not owner-managed and not family-led.", "contradicted"),
        ("Weder eigentümer- noch familiengeführt, aber inhabergeführt.", "unclear"),
    ],
)
def test_leadership_negation_does_not_cross_independent_conjunction(quote, expected):
    assert boolean_verdict("owner_or_family_led", True, quote)[0] == expected


def test_ownership_and_multiple_dated_projects_are_distinct_nonconflicting_facts():
    quote = "Musterwerk GmbH ist in Familienbesitz. Musterwerk GmbH startete 2021 Projekt Alpha und 2026 Projekt Beta."
    candidates = [extraction_fact(field="family_ownership", value_json="true", quote=quote)]
    candidates.extend(
        extraction_fact(field="ai_project", value_json=json.dumps(value), quote=quote, information_as_of=year)
        for value, year in [("Projekt Alpha", "2021"), ("Projekt Beta", "2026")]
    )
    bundle = validate_extraction(
        Extraction(facts=candidates, ambiguous_participant_ids=[], gaps=[]),
        [source(quote)],
        ResearchQuery.from_input(meeting()),
    )
    assert len(bundle.facts) == 3
    assert bundle.facts[1].status == "historical" and bundle.facts[2].status == "verified"
    assert not _value_supported("owner_or_family_led", True, quote)


def pdf_bytes(*, text="Musterwerk GmbH has 120 employees.", pages=1, encrypted=False):
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    options = (
        {"encryption": fitz.PDF_ENCRYPT_AES_256, "owner_pw": "controlled", "user_pw": "controlled"}
        if encrypted
        else {}
    )
    data = document.tobytes(**options)
    document.close()
    return data


async def test_pdf_real_isolated_parser_returns_text():
    text, title = await pdf_text(pdf_bytes())
    assert "Musterwerk GmbH has 120 employees" in text and title


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"encrypted": True}, "encrypted"),
        ({"text": ""}, "scan_no_text"),
        ({"pages": 201, "text": ""}, "page_limit"),
    ],
)
async def test_pdf_encrypted_scans_and_page_limits_are_explicit(kwargs, code):
    with pytest.raises(EvidenceError, match=code):
        await pdf_text(pdf_bytes(**kwargs))


async def test_pdf_limit_three_and_15mib_apply_before_parsing(monkeypatch):
    async def parse(*args):
        return "Public company report text with concrete evidence.", "Report"

    monkeypatch.setattr("atp.evidence.pdf_text", parse)
    responses = [
        EvidenceResponse(headers={"Content-Type": "application/pdf"}, content=b"controlled") for _ in range(4)
    ]
    fetcher = EvidenceFetcher(
        resolver=lambda *args: ["93.184.216.34"], connection_factory=factory_for(responses, [])
    )
    budget = Budget(real_settings())
    for index in range(3):
        assert (await fetcher.fetch(f"https://example.com/report-{index}.pdf", budget)).verified
    with pytest.raises(EvidenceError, match="pdf_count_limit"):
        await fetcher.fetch("https://example.com/report-4.pdf", budget)
    big = EvidenceFetcher(
        resolver=lambda *args: ["93.184.216.34"],
        connection_factory=factory_for(
            [
                EvidenceResponse(
                    headers={"Content-Type": "application/pdf", "Content-Length": str(MAX_PDF_BYTES + 1)}
                )
            ],
            [],
        ),
    )
    with pytest.raises(EvidenceError, match="size_limit"):
        await big.fetch("https://example.com/report.pdf", Budget(real_settings()))


@pytest.mark.parametrize("cancel", [False, True])
async def test_pdf_subprocess_is_killed_on_timeout_and_cancellation(monkeypatch, cancel):
    started = asyncio.Event()

    class Child:
        returncode = None
        killed = False

        async def communicate(self, data):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    child = Child()

    async def spawn(*args, **kwargs):
        return child

    monkeypatch.setattr("atp.evidence.asyncio.create_subprocess_exec", spawn)
    task = asyncio.create_task(pdf_text(b"controlled", timeout=0.02))
    if cancel:
        await started.wait()
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else EvidenceError):
        await task
    assert child.killed and child.returncode == -9
