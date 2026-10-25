"""A readable short handout from the saved reader projection, without regeneration."""

import re
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from .briefing_content import attention_notes, presentation_overview


def render_compact_pdf(result):
    # Import lazily: the canonical renderer owns the shared font/escaping contract.
    from .pdf import ASSETS, INK, LINE, MUTED, ORANGE, RELATIONSHIPS, TYPES, _date, _styles, _xml

    styles = _styles()
    body = ParagraphStyle("Brief body", parent=styles["body"], fontSize=10, leading=14.5, spaceAfter=9)
    heading = ParagraphStyle(
        "Brief heading",
        parent=body,
        fontName="ATP-Semibold",
        textColor=INK,
        spaceBefore=12,
        spaceAfter=5,
        keepWithNext=True,
    )
    opener = ParagraphStyle(
        "Brief opener",
        parent=body,
        fontSize=14,
        leading=20,
        textColor=INK,
        leftIndent=14,
        borderColor=ORANGE,
        borderWidth=0,
        borderPadding=10,
        backColor=colors.HexColor("#f2ece0"),
        spaceBefore=8,
        spaceAfter=16,
    )
    stream, story = BytesIO(), []

    def p(text, style=body):
        story.append(Paragraph(_xml(text, bold=style in (heading, styles["title"])), style))

    p(result.input.company.name, styles["title"])
    p(
        TYPES.get(result.input.meeting_type, "Anlass ungeklärt")
        + " · "
        + RELATIONSHIPS[result.input.relationship]
        + " · "
        + _date(result.input.scheduled_at),
        styles["small"],
    )
    if result.status == "partial":
        p("Teilergebnis: Recherche nur teilweise abgeschlossen.", styles["small"])
    if result.fixture_mode:
        p("TESTMODUS · Synthetisches Beispiel, kein Nachweis über ein reales Unternehmen.", styles["small"])
    p("Dein Gesprächsziel", heading)
    p(result.input.goal or "Erwartungen und nächsten Schritt gemeinsam klären.")
    for note in attention_notes(result):
        p(note, styles["small"])
    overview = {item.category: item for item in presentation_overview(result)}
    for key, title in (
        ("company", "Unternehmen in 20 Sekunden"),
        ("people", "Dein Gegenüber"),
        ("development", "Für dieses Gespräch relevant"),
    ):
        if key in overview:
            p(title, heading)
            p(overview[key].text)
    if "opener" in overview:
        p("Dein Gesprächseinstieg · Vorschlag", heading)
        p(re.sub(r"^Gesprächsvorschlag:\s*", "", overview["opener"].text), opener)
    story.append(HRFlowable(width="100%", thickness=0.5, color=LINE, spaceBefore=4, spaceAfter=4))
    p("Die drei wichtigsten Gesprächsfragen", heading)
    for number, question in enumerate(result.questions[:3], 1):
        p(f"{number:02d}   {question.text}")
    if "next_step" in overview:
        p("Einordnung und nächster Schritt", heading)
        p(overview["next_step"].text)
    story.append(Spacer(1, 8))
    p(
        "Quellen, weitere Fragen und Recherchegrenzen stehen im vollständigen Export und in der HTML-Datei.",
        styles["small"],
    )
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=50,
        rightMargin=50,
        topMargin=80,
        bottomMargin=53,
        title="Kurzbriefing · " + result.input.company.name,
        author="AI Transformation Partners",
    )
    logo = ImageReader(str(ASSETS / "atp-logo.png"))
    logo_width, logo_height = logo.getSize()

    def page(canvas, doc):
        width, height = A4
        canvas.saveState()
        canvas.drawImage(logo, 50, height - 44, width=90, height=90 * logo_height / logo_width, mask="auto")
        canvas.setFont("ATP", 7)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width - 50, height - 38, "GESPRÄCHSBRIEFING · INTERN / VERTRAULICH")
        canvas.setStrokeColor(LINE)
        canvas.line(50, height - 59, width - 50, height - 59)
        canvas.line(50, 40, width - 50, 40)
        canvas.setFont("ATP", 6.5)
        canvas.drawString(50, 27, f"Version {result.result_version} · Stand {_date(result.generated_at)}")
        canvas.drawRightString(width - 50, 27, f"{doc.page:02d}")
        canvas.restoreState()

    document.build(story, onFirstPage=page, onLaterPages=page)
    return stream.getvalue()
