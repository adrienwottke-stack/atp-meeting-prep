"""Deterministic ATP PDF renderer for the immutable canonical BriefingResult.

No network, model calls, file writes or regeneration of content occur here. All
provider/user strings are escaped before entering ReportLab's limited XML parser.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .briefing_content import attention_notes, presentation_overview, reader_result
from .models import BriefingResult, Claim
from .report_notes import OVERVIEW_LABELS, quality_notes, report_notes

ASSETS = Path(__file__).parent / "static"
PAPER = colors.HexColor("#faf7f0")
INK = colors.HexColor("#16140e")
TEXT = colors.HexColor("#4b463a")
MUTED = colors.HexColor("#756f5f")
ORANGE = colors.HexColor("#cf4f27")
LINE = colors.HexColor("#d7cdb8")
WASH = colors.HexColor("#efe8d8")
TYPES = {
    "setup": "Setup-Meeting",
    "audit_consultation": "Audit-Beratungsgespräch",
    "enablement_consultation": "Enablement-Beratungsgespräch",
    "project_review": "Projektbesprechung",
}
RELATIONSHIPS = {
    "new_contact": "Neukontakt",
    "existing_customer": "Bestandskunde",
    "ongoing_project": "Laufendes Projekt",
    "unknown": "Beziehung unbekannt",
}
ICP = {"matching": "ICP passend", "deviating": "ICP abweichend", "unclear": "ICP ungeklärt"}
KINDS = {
    "fact": "Belegte Tatsache",
    "user_confirmed": "Bestätigte Eingabe",
    "hypothesis": "Hypothese",
    "unknown": "Unbekannt",
}
PERSON_STATUS = {
    "confirmed": "Zuordnung bestätigt",
    "ambiguous": "Zuordnung mehrdeutig",
    "unresolved": "Zuordnung ungeklärt",
}
SERVICES = {
    "entry": "Ersteinschätzung und Setup",
    "audit": "AI Audit",
    "enablement": "AI Enablement",
    "training": "KI Schulungen",
    "workshop": "Workshop für Führungskräfte",
    "keynote": "Impulse und Keynotes",
}


@lru_cache(maxsize=1)
def font_setup() -> dict[str, set[int]]:
    """Register bundled Switzer; use a documented system fallback per glyph.

    Missing non-Latin glyphs are rendered as visible U+ code points rather than
    silently producing black squares. The web view retains the original string.
    """
    result = {}
    for name, filename in (("ATP", "Switzer-Regular.ttf"), ("ATP-Semibold", "Switzer-Semibold.ttf")):
        font = TTFont(name, str(ASSETS / filename))
        pdfmetrics.registerFont(font)
        result[name] = set(font.face.charToGlyph)
    pdfmetrics.registerFontFamily(
        "ATP", normal="ATP", bold="ATP-Semibold", italic="ATP", boldItalic="ATP-Semibold"
    )
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.exists():
            fallback = TTFont("ATP-Fallback", str(path))
            pdfmetrics.registerFont(fallback)
            result["ATP-Fallback"] = set(fallback.face.charToGlyph)
            break
    return result


def _plain(value) -> str:
    if value is None or value == "":
        return "Unbekannt"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_plain(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(_plain(v) for v in value)
    return str(value)


def _xml(value, *, bold: bool = False) -> str:
    text = _plain(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[\u2010-\u2015]", "-", text).replace("\u00a0", " ")
    fonts = font_setup()
    primary = "ATP-Semibold" if bold else "ATP"
    segments: list[str] = []
    for char in text:
        if char == "\n":
            segments.append("<br/>")
        elif ord(char) in fonts[primary] or char in "\t\r":
            segments.append(escape(char))
        elif ord(char) in fonts.get("ATP-Fallback", set()):
            segments.append('<font name="ATP-Fallback">' + escape(char) + "</font>")
        else:
            segments.append(f"[U+{ord(char):04X}]")
    return "".join(segments)


def _safe_url(value: str) -> str | None:
    if any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme.lower() in {"http", "https"} and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return None


def _date(value: datetime | None) -> str:
    if value is None:
        return "Nicht angegeben"
    try:
        value = value.astimezone(ZoneInfo("Europe/Berlin"))
    except ZoneInfoNotFoundError:
        pass  # explicit original UTC suffix if tzdata is unavailable
    return value.strftime("%d.%m.%Y, %H:%M %Z")


def _styles() -> dict[str, ParagraphStyle]:
    font_setup()
    base = ParagraphStyle(
        "ATP body",
        fontName="ATP",
        fontSize=9.5,
        leading=14,
        textColor=TEXT,
        spaceAfter=8,
        splitLongWords=True,
        allowWidows=0,
        allowOrphans=0,
    )
    return {
        "body": base,
        "small": ParagraphStyle(
            "ATP small", parent=base, fontSize=8, leading=11, textColor=MUTED, spaceAfter=6
        ),
        "label": ParagraphStyle(
            "ATP label",
            parent=base,
            fontName="ATP-Semibold",
            fontSize=8,
            leading=11,
            textColor=ORANGE,
            spaceBefore=13,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "title": ParagraphStyle(
            "ATP title",
            parent=base,
            fontName="ATP-Semibold",
            fontSize=27,
            leading=31,
            textColor=INK,
            spaceAfter=13,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "ATP h2",
            parent=base,
            fontName="ATP-Semibold",
            fontSize=19,
            leading=23,
            textColor=INK,
            spaceBefore=5,
            spaceAfter=13,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "ATP h3",
            parent=base,
            fontName="ATP-Semibold",
            fontSize=12,
            leading=16,
            textColor=INK,
            spaceBefore=14,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "point": ParagraphStyle("ATP point", parent=base, leftIndent=11, firstLineIndent=-11, spaceAfter=8),
        "recommendation": ParagraphStyle(
            "ATP recommendation",
            parent=base,
            fontName="ATP-Semibold",
            fontSize=19,
            leading=23,
            textColor=ORANGE,
            spaceAfter=12,
            keepWithNext=True,
        ),
        "quote": ParagraphStyle(
            "ATP quote",
            parent=base,
            fontSize=8.5,
            leading=12,
            leftIndent=12,
            borderColor=LINE,
            borderWidth=1,
            borderPadding=8,
            spaceBefore=5,
            spaceAfter=12,
        ),
    }


def render_pdf(result: BriefingResult, *, compact: bool = False) -> bytes:
    """Render the exact saved result version, including all six required areas."""
    if not isinstance(result, BriefingResult):
        result = BriefingResult.model_validate(result)
    result = reader_result(result)
    if compact:
        from .compact_pdf import render_compact_pdf

        return render_compact_pdf(result)
    styles = _styles()
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        rightMargin=55,
        leftMargin=55,
        topMargin=92,
        bottomMargin=57,
        title="Gesprächsbriefing - " + result.input.company.name,
        author="AI Transformation Partners GmbH",
        subject=f"Meeting: {result.meeting_id}; Recherchelauf: {result.run_id}; Briefing: {result.briefing_id}",
        pageCompression=1,
    )
    story = []
    source_numbers = {source.source_id: i + 1 for i, source in enumerate(result.sources)}
    facts = {fact.fact_id: fact for fact in result.facts}

    def p(text, style="body", raw=False):
        story.append(
            Paragraph(
                text if raw else _xml(text, bold=style in {"title", "h2", "h3", "recommendation"}),
                styles[style],
            )
        )

    def label(text):
        p(text.upper(), "label")

    def section(code, title):
        story.append(Spacer(1, 11))
        story.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceAfter=12))
        label(code)
        p(title, "h2")

    def refs(ids):
        links = []
        for source_id in dict.fromkeys(ids):
            if source_id in source_numbers:
                number = source_numbers[source_id]
                links.append(f'<link href="#source-{number}" color="#a93c1a">[{number}]</link>')
        return " " + " ".join(links) if links else ""

    def claim(c: Claim):
        ids = [*c.evidence_ids]
        for fact_id in c.fact_ids:
            if fact_id in facts:
                ids.extend(facts[fact_id].evidence_ids)
        p(
            '<font name="ATP-Semibold" color="#a93c1a">'
            + _xml(KINDS[c.kind], bold=True)
            + ": </font>"
            + _xml(c.text)
            + refs(ids),
            raw=True,
        )

    def bullets(values, empty):
        if values:
            for value in values:
                p("- " + value, "point")
        else:
            p(empty, "small")

    label("Gesprächsbriefing / Vertraulich")
    p(result.input.company.name, "title")
    p(
        TYPES.get(result.input.meeting_type, "Anlass ungeklärt")
        + " / "
        + RELATIONSHIPS[result.input.relationship]
    )
    if result.input.relationship == "existing_customer":
        p(
            "Bestandskunden-Anlass: "
            + {
                "unspecified": "Noch nicht eingeordnet",
                "existing_obligation": "Bestehende Verpflichtung",
                "new_sales_inquiry": "Neue Vertriebsanfrage",
            }.get(getattr(result.input, "relationship_context", "unspecified"), "Noch nicht eingeordnet")
        )
    p(
        f"Version {result.result_version} / {'Teilergebnis' if result.status == 'partial' else 'Erstellung abgeschlossen'} / Erstellt {_date(result.generated_at)}",
        "small",
    )
    p("Meeting: " + _date(result.input.scheduled_at), "small")
    if result.fixture_mode:
        p(
            "TESTMODUS: Kontrollierte synthetische Quellen. Kein Nachweis über ein reales Unternehmen.",
            "label",
        )
    quality_label, quality_reasons = quality_notes(result.model_dump(mode="json"))
    label("Dein Gesprächsziel")
    p(result.input.goal or "Erwartungen und nächsten Schritt gemeinsam klären.")
    for note in attention_notes(result):
        p(note, "small")

    overview = presentation_overview(result)
    if overview:
        label("Auf einen Blick")
        for item in overview[:5]:
            label(OVERVIEW_LABELS.get(item.category, "Auf einen Blick"))
            p(item.text)
    else:
        label("Auf einen Blick")
        bullets(result.summary[:5], "Keine zusätzlichen Kernpunkte hinterlegt.")
        label("Meeting-Empfehlung")
        p(result.recommendation.value, "recommendation")
        p(result.recommendation.reason)
        p(ICP[result.icp.status], "h3")
        label("Wichtigste offene Punkte")
        bullets(
            list(dict.fromkeys([*result.icp.open_points, *result.gaps]))[:5],
            "Keine zusätzlichen offenen Punkte hinterlegt.",
        )

    label("Die drei wichtigsten Gesprächsfragen")
    for number, question in enumerate(result.questions[:3], 1):
        p(f"{number}. {question.text}")
    p(
        "Quellen und weitere Details stehen im vollständigen Export und in der HTML-Datei."
        if compact
        else "Vertiefung, Zielgruppenprüfung und vollständige Belege folgen im Anhang.",
        "small",
    )
    core_end = len(story)
    story.append(PageBreak())
    label("Anhang / bei Bedarf nachlesen")
    section("A01", "Unternehmensüberblick")
    for company_claim in result.company:
        claim(company_claim)
    if not result.company:
        p("Keine hinreichend belegten Unternehmensangaben verfügbar.")

    section("A02", "Ansprechpartner im Überblick")
    for person in result.people:
        p(person.name, "h3")
        p(PERSON_STATUS[person.status], "small")
        label("Rolle")
        claim(person.role)
        label("Beruflicher Werdegang")
        for career_claim in person.career:
            claim(career_claim)
        if not person.career:
            p("Keine belegten Karrieredetails verfügbar.", "small")
        label("Berufliche Themen")
        for topic in person.topics:
            claim(topic)
        if not person.topics:
            p("Keine belegten beruflichen Themen verfügbar.", "small")
        if person.gaps:
            label("Offene Punkte zur Person")
            bullets(person.gaps, "")

    section("A03", "Anlass und ATP-Ansatzpunkte")
    p("Anlass: " + TYPES.get(result.input.meeting_type, "Ungeklärt"))
    p("Gesprächsziel: " + (result.input.goal or "Nicht zusätzlich angegeben."))
    for opportunity in result.opportunities:
        p(SERVICES.get(opportunity.service_id, opportunity.service_id), "h3")
        label("Beobachtung")
        p(_xml(opportunity.observation) + refs(opportunity.evidence_ids), raw=True)
        label("Hypothese / im Gespräch prüfen")
        p(opportunity.hypothesis)
        label("Prüfungsfrage")
        p(opportunity.validation_question)
    if not result.opportunities:
        p("Kein belastbarer Ansatzpunkt abgeleitet. Die Fragen klären den möglichen Bedarf.")

    section("A04", "Fragen für das Gespräch")
    for number, question in enumerate(result.questions, 1):
        if number == 1:
            label("Die drei wichtigsten Fragen")
        elif number == 4:
            label("Weitere Fragen")
        p(f"{number:02d} / {question.text}", "h3")
        p("Zweck: " + question.purpose)
        p("Bezug: " + question.reference, "small")

    section("A05", "Gesprächs-Storyline")
    for phase in result.storyline:
        p(phase.phase, "h3")
        p("Ziel: " + phase.goal)
        p("Gesprächsimpuls: " + phase.impulse)

    section("ICP", "Zielgruppenpassung")
    p(ICP[result.icp.status], "h3")
    for criterion in result.icp.criteria:
        p(
            criterion.label
            + " / "
            + {"matching": "Erfüllt", "deviating": "Abweichend", "unclear": "Ungeklärt"}[criterion.status],
            "h3",
        )
        p("Wert: " + _plain(criterion.value))
        p(_xml(criterion.reason) + refs(criterion.evidence_ids), raw=True)
        subject = result.input.company.name if criterion.subject_ref == "company" else criterion.subject_ref
        origin = {
            "web_verified": "Web-Beleg",
            "user_confirmed": "Bestätigte Eingabe",
            "unknown": "Unbekannt",
        }.get(criterion.provenance, criterion.provenance)
        p(
            f"Bezug: {subject} / Informationsstand: {criterion.information_as_of or 'Unbekannt'} / Herkunft: {origin}",
            "small",
        )
        if criterion.deviation_kind:
            p(
                "Abweichungsart: "
                + {
                    "corridor": "Größenkorridor",
                    "target_structure": "Zielstruktur",
                    "exclusion": "Bestätigter Ausschluss",
                }[criterion.deviation_kind],
                "small",
            )
    p("Passender Leistungseinstieg", "h3")
    p(result.icp.entry_reason or "Noch zu klären.")
    if result.icp.entry_service_ids:
        story[-1].keepWithNext = True
        p("Leistungen: " + ", ".join(SERVICES.get(s, s) for s in result.icp.entry_service_ids))

    section("A06", "Quellen und Wissenslücken")
    p(quality_label, "h3")
    bullets(list(dict.fromkeys(quality_reasons)), "Keine zusätzlichen Qualitätshinweise.")
    notes = report_notes(result.model_dump(mode="json"))
    if result.research_coverage:
        p("Rechercheabdeckung", "h3")
        for coverage in result.research_coverage:
            area = {
                "company": "Unternehmen",
                "developments": "Entwicklungen",
                "people": "Ansprechpartner",
                "icp": "Zielgruppenprüfung",
            }[coverage.area]
            status = {
                "covered": "Abgedeckt",
                "partial": "Teilweise abgedeckt",
                "missing": "Öffentlich nicht ausreichend belegt",
            }[coverage.status]
            p(area + ": " + status)
            for reason in coverage.reasons:
                p("- " + reason, "point")
    p("Offene Punkte", "h3")
    bullets(notes["gaps"], "Keine weiteren Wissenslücken hinterlegt.")
    p("Bestätigte Angaben und offene Entscheidungen", "h3")
    bullets(notes["resolutions"], "Keine zusätzlichen Nutzerentscheidungen hinterlegt.")
    p("Grenzen dieses Laufs", "h3")
    bullets(notes["limits"], "Keine zusätzlichen technischen Einschränkungen hinterlegt.")
    p("Quellenverzeichnis", "h3")
    p(
        "Abrufzeit und Informationsstand werden getrennt geführt. Ein heutiger Abruf bestätigt keine aktuelle Gültigkeit.",
        "small",
    )
    for number, source in enumerate(result.sources, 1):
        # Anchor is generated locally; no external text is used as markup.
        p(f'<a name="source-{number}"/>' + _xml(f"[{number}] {source.title}", bold=True), "h3", raw=True)
        url = _safe_url(source.url)
        url_text = _xml(source.url)
        if url:
            p("<link href=" + quoteattr(url) + ' color="#a93c1a">' + url_text + "</link>", raw=True)
        else:
            p(source.url)
        p(
            f"Abgerufen: {_date(source.retrieved_at)} / Informationsstand: {source.information_as_of or 'Unbekannt'} / Veröffentlicht: {source.published_at or 'Unbekannt'}",
            "small",
        )
        p("Beleg geprüft" if source.verified else "Nicht hinreichend geprüft", "small")
        if source.excerpt:
            p(source.excerpt, "quote")
        if source.limitation:
            p("Einschränkung: " + source.limitation, "small")
    if not result.sources:
        p("Keine prüfbaren Web-Belege verfügbar.")

    if result.memory:
        section("Wissen", "Berücksichtigtes Wissen")
        for entry in result.memory.get("entries", []):
            p(entry["text"])
            p(
                f"{entry['context_name']} · {entry['source']} · Stand {entry.get('information_as_of') or entry.get('confirmed_at')} · Version {entry['version']}",
                "small",
            )
        if not result.memory.get("entries"):
            p("Keine bestätigten Wissenseinträge vorhanden.")
    section("Ergebnisstand", "Versionen und Nachvollziehbarkeit")
    p(
        "Die Terminempfehlung ist keine automatische Terminaktion. Eine gegebenenfalls später gespeicherte menschliche Entscheidung wird separat in der Anwendung geführt."
    )
    p(
        f"Ergebnisversion: {result.result_version} / Profil: {result.profile_version}\n"
        f"Profil-Hash: {result.profile_hash}\n"
        f"Prompt: {result.prompt_version} / Regeln: {result.rule_version} / Darstellung: {result.presentation_version}\n"
        f"Modelle: {_plain(result.models)}",
        "small",
    )

    logo = ImageReader(str(ASSETS / "atp-logo.png"))
    logo_width, logo_height = logo.getSize()

    def page(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, width, height, stroke=0, fill=1)
        canvas.drawImage(logo, 55, height - 52, width=87, height=87 * logo_height / logo_width, mask="auto")
        canvas.setFont("ATP-Semibold", 6.5)
        canvas.setFillColor(TEXT)
        canvas.drawString(160, height - 43, "MEETING-VORBEREITUNG")
        canvas.setFont("ATP", 6)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width - 55, height - 43, "INTERN / VERTRAULICH")
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(55, height - 64, width - 55, height - 64)
        canvas.line(55, 43, width - 55, 43)
        canvas.setFillColor(ORANGE)
        canvas.circle(56.5, 29.5, 1.3, fill=1, stroke=0)
        canvas.setFillColor(MUTED)
        canvas.setFont("ATP", 6)
        canvas.drawString(65, 27, f"ATP / GESPRÄCHSBRIEFING / VERSION {result.result_version}")
        canvas.setFillColor(TEXT)
        canvas.drawRightString(width - 55, 27, f"{doc.page:02d}")
        canvas.restoreState()

    document.build(story[:core_end] if compact else story, onFirstPage=page, onLaterPages=page)
    return stream.getvalue()
