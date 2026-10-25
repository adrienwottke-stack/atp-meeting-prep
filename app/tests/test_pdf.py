"""Semantic, security and long-layout PDF verification with synthetic data only."""

from __future__ import annotations

import pymupdf
import pytest

from atp.briefing_content import build_overview
from atp.models import (
    BriefingResult,
    Claim,
    Company,
    Criterion,
    Fact,
    ICPResult,
    MeetingInput,
    Opportunity,
    Person,
    PersonProfile,
    QualityAssessment,
    Question,
    Recommendation,
    RecommendationValue,
    Source,
    StoryPhase,
)
from atp.pdf import font_setup, render_pdf


def sample_result(long=False) -> BriefingResult:
    company = "Müller & Söhne Präzisionstechnik GmbH"
    if long:
        company += " Gesellschaft für internationale Fertigung und verantwortungsvolle Digitalisierung" * 4
    people = [Person(participant_id="p1", name="Lea Sommer"), Person(participant_id="p2", name="Tobias Groß")]
    source = Source(
        source_id="s1",
        title="Öffentliches Unternehmensprofil & Leistungen",
        url="https://musterwerk.example/unternehmen?thema=Pr%C3%A4zision&jahr=2026",
        excerpt="Müller & Söhne fertigt Baugruppen. 120 Mitarbeitende.",
        verified=True,
        information_as_of="2026",
    )
    fact = Fact(
        fact_id="f1",
        field="business_model",
        value="Baugruppenfertigung",
        status="verified",
        provenance="web_verified",
        evidence_ids=["s1"],
    )
    c = Claim(text="Müller & Söhne fertigt Baugruppen.", kind="fact", fact_ids=["f1"])
    result = BriefingResult(
        meeting_id="meeting-1",
        run_id="run-1",
        result_version=3,
        status="completed",
        profile_version="ATP-2026-10-15.2",
        profile_hash="abc123",
        models={"synthesis": "fixture-only"},
        fixture_mode=True,
        input=MeetingInput(
            company=Company(name=company, website="https://musterwerk.example"),
            people=people,
            meeting_type="setup",
            relationship="new_contact",
            goal="Prozesse und nächste Schritte klären",
            confirmed=True,
            public_identifiers_confirmed=True,
        ),
        summary=[
            "Baugruppenfertigung mit 120 Mitarbeitenden.",
            "Zwei Ansprechpartner getrennt zugeordnet.",
            "Führung muss vorab geklärt werden.",
        ],
        company=[c, Claim(text='<script>alert("x")</script> & <b>kein HTML</b>', kind="hypothesis")],
        people=[
            PersonProfile(
                participant_id=p.participant_id,
                name=p.name,
                status="unresolved",
                role=Claim(text="Rolle nicht belegt", kind="unknown"),
                career=[],
                topics=[],
                gaps=["Aktuelle berufliche Zuordnung offen."],
            )
            for p in people
        ],
        opportunities=[
            Opportunity(
                observation="Prozessbezug ist belegt.",
                evidence_ids=["s1"],
                hypothesis="Ein AI Audit könnte zur Priorisierung beitragen.",
                service_id="audit",
                validation_question="Welche Prozesse verursachen wiederkehrenden Aufwand?",
            )
        ],
        questions=[
            Question(
                text=f"Welche Prozessfrage {i} möchten Sie klären?",
                purpose="Erwartung konkretisieren",
                reference="Offene Prozesspriorität",
            )
            for i in range(1, 6)
        ],
        storyline=[
            StoryPhase(
                phase=p, goal="Ziel gemeinsam festhalten", impulse="Was wäre ein sinnvoller nächster Schritt?"
            )
            for p in ["Einstieg", "Ausgangslage und Ziel", "Vertiefung", "Nächster Schritt"]
        ],
        icp=ICPResult(
            status="unclear",
            criteria=[
                Criterion(
                    field="employees_total",
                    label="Gesamtmitarbeitende",
                    value=120,
                    status="matching",
                    evidence_ids=["s1"],
                    provenance="web_verified",
                    reason="Belegter Wert im Korridor.",
                )
            ],
            open_points=["Führung ungeklärt."],
            entry_service_ids=["audit"],
            entry_reason="Wissensarbeiter getrennt klären.",
        ),
        recommendation=Recommendation(
            value=RecommendationValue.CLARIFY,
            reason="Die Führung könnte die Terminentscheidung verändern.",
            rule="qualification_missing",
        ),
        sources=[source],
        facts=[fact],
        gaps=["Führung ungeklärt."],
        limits=[],
    )
    if long:
        result.company.extend(
            [
                Claim(
                    text="Langer belegfreier Kontext als Hypothese: "
                    + "Digitalisierung und verantwortliche Prozessgestaltung. " * 35,
                    kind="hypothesis",
                )
                for _ in range(5)
            ]
        )
        for i in range(12):
            result.sources.append(
                Source(
                    source_id=f"extra-{i}",
                    title=f"Ergänzende synthetische Quelle {i}",
                    url="https://musterwerk.example/" + "langer-pfad-" * 38 + f"?quelle={i}&kontext=beleg",
                    excerpt="Eine lange relevante Fundstelle zur kontrollierten Prüfung. " * 24,
                    limitation="Synthetischer Layouttest",
                    verified=False,
                )
            )
    return result


def test_pdf_canonical_content_and_escaping():
    result = sample_result()
    data = render_pdf(result)
    assert data.startswith(b"%PDF-")
    reader = pymupdf.open(stream=data, filetype="pdf")
    text = "\n".join(page.get_text() for page in reader)
    for content in [
        result.input.company.name,
        "Lea Sommer",
        "Tobias Groß",
        "A01",
        "A02",
        "A03",
        "A04",
        "A05",
        "A06",
        "ICP ungeklärt",
        "Vorab klären",
        "TESTMODUS",
        "Version 3",
        result.profile_version,
        "Quellenverzeichnis",
        "Informationsstand",
        "120",
        "Nächster Schritt",
    ]:
        assert content in text
    assert '<script>alert("x")</script> & <b>kein HTML</b>' in text
    assert len(reader) >= 3
    assert len(result.summary) <= 5
    assert result.meeting_id in reader.metadata["subject"]
    assert result.run_id in reader.metadata["subject"]
    assert result.briefing_id in reader.metadata["subject"]


def test_source_links_and_internal_references():
    result = sample_result()
    doc = pymupdf.open(stream=render_pdf(result), filetype="pdf")
    links = [link for page in doc for link in page.get_links()]
    assert any(link.get("uri") == result.sources[0].url for link in links)
    assert any(link.get("kind") == pymupdf.LINK_GOTO for link in links)


def test_unsafe_source_url_is_text_only():
    result = sample_result()
    with pytest.raises(ValueError):
        result.sources[0].url = 'javascript:alert("no")'
    # Deliberate bypass exercises renderer defence for an already-loaded object.
    object.__setattr__(result.sources[0], "url", 'javascript:alert("no")')
    doc = pymupdf.open(stream=render_pdf(result), filetype="pdf")
    assert all(not link.get("uri", "").startswith("javascript:") for page in doc for link in page.get_links())
    assert "javascript:" in "".join(page.get_text() for page in doc)


def test_long_names_urls_and_multi_page_layout(tmp_path):
    data = render_pdf(sample_result(long=True))
    doc = pymupdf.open(stream=data, filetype="pdf")
    assert len(doc) >= 7
    assert len(doc) < 30
    for page in doc:
        for x0, y0, x1, y1, word, *_ in page.get_text("words"):
            assert x0 >= 50, (page.number, word, x0)
            assert x1 <= page.rect.width - 49, (page.number, word, x1)
            assert y0 >= 20
            assert y1 < page.rect.height - 15
    assert "langer-pfad" in "".join(page.get_text() for page in doc)
    (tmp_path / "long-layout.pdf").write_bytes(data)


def test_font_coverage_for_german_and_urls():
    fonts = font_setup()
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzÄÖÜäöüß0123456789:/?=&%#@._-[]()"
    for font in ("ATP", "ATP-Semibold"):
        assert all(ord(char) in fonts[font] for char in chars)


def test_null_controls_and_unusual_glyphs_do_not_break_export():
    result = sample_result()
    result.company.append(Claim(text="Kontrollzeichen\x00 und Sonderzeichen 漢字 🛠", kind="unknown"))
    data = render_pdf(result)
    assert data.startswith(b"%PDF-")
    assert "Kontrollzeichen" in "".join(page.get_text() for page in pymupdf.open(stream=data, filetype="pdf"))


@pytest.mark.parametrize(
    "state,label",
    [
        ("sufficient", "Für den bestätigten Anlass ausreichend belegt"),
        ("limited", "Mit Einschränkungen verwendbar"),
        ("clarification_required", "Wesentliche Klärung offen"),
        ("not_assessed", "Fachliche Verwendbarkeit nicht bewertet"),
    ],
)
def test_pdf_quality_and_user_resolution_match_saved_result(state, label):
    result = sample_result()
    result.quality = QualityAssessment(state=state, reasons=["Aktuelle Rolle weiterhin offen."])
    result.resolutions = {
        "goal": {
            "question": "Welches Ziel?",
            "action": "provide",
            "value": "Prioritäten abstimmen",
            "confirmed_by": "test-user",
            "confirmed_at": "2026-10-15T10:00:00Z",
        },
        "employees": {"field": "employees_total", "action": "leave_unknown", "value": ""},
    }
    before = result.model_dump(mode="json")
    doc = pymupdf.open(stream=render_pdf(result), filetype="pdf")
    text = " ".join(" ".join(page.get_text().split()) for page in doc)
    assert label in text and "Aktuelle Rolle weiterhin offen." in text
    assert "Erstellung abgeschlossen" in text
    assert "Prioritäten abstimmen / Bestätigte Nutzereingabe; kein Web-Beleg." in text
    assert "test-user" in text and "2026-10-15T10:00:00Z" in text
    assert "Bewusst ungeklärt gelassen; keine Tatsachenbestätigung." in text
    assert result.model_dump(mode="json") == before


def test_pdf_overview_first_page_and_detail_order(tmp_path):
    result = sample_result()
    result.overview = build_overview(result)
    result.presentation_version = "atp-presentation-2"
    data = render_pdf(result)
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = " ".join(" ".join(page.get_text().split()) for page in doc)
    first = doc[0].get_text()
    assert "UNTERNEHMEN IN 20 SEKUNDEN" in first
    assert text.count(result.recommendation.value) == 1
    assert "DEIN GESPRÄCHSEINSTIEG" in text and "DIE DREI WICHTIGSTEN FRAGEN" in text
    assert text.index("Unternehmensüberblick") < text.index("Ansprechpartner im Überblick")
    assert text.index("Gesprächs-Storyline") < text.rindex("Zielgruppenpassung")
    doc[0].get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(tmp_path / "overview-first-page.png")
    (tmp_path / "overview.pdf").write_bytes(data)
