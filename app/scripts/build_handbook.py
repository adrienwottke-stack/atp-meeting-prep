"""Build the maintained Markdown handbook as a versioned ATP PDF, offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer
from reportlab.platypus.tableofcontents import TableOfContents

APP_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = APP_ROOT / "docs/handbook"
PAPER, INK, ORANGE = (colors.HexColor(v) for v in ("#faf7f0", "#16140e", "#cf4f27"))


def git_version():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=APP_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unversionierte-paketkopie"


def inline(text):
    text = escape(text)
    text = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return re.sub(r"\[([^]]+)\]\((https?://[^)]+)\)", r'<link href="\2" color="#cf4f27">\1</link>', text)


class HandbookDoc(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "HandbookChapter":
            anchor = "chapter-" + hashlib.sha256(flowable.getPlainText().encode()).hexdigest()[:12]
            self.canv.bookmarkPage(anchor)
            self.canv.addOutlineEntry(flowable.getPlainText(), anchor, level=0)
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page, anchor))


def build_handbook(output: Path, *, source_dir=SOURCE_DIR, revision=None):
    source_dir, output = Path(source_dir), Path(output)
    files = sorted(source_dir.glob("[0-9][0-9]-*.md"))
    if not files:
        raise ValueError("NO_HANDBOOK_SOURCES")
    revision = revision or git_version()
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    digest = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()
    assets = APP_ROOT / "src/atp/static"
    pdfmetrics.registerFont(TTFont("HandbookRegular", str(assets / "Switzer-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("HandbookSemibold", str(assets / "Switzer-Semibold.ttf")))
    pdfmetrics.registerFontFamily("HandbookRegular", normal="HandbookRegular", bold="HandbookSemibold")
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "HandbookBody", fontName="HandbookRegular", fontSize=10, leading=15, textColor=INK, spaceAfter=9
    )
    chapter = ParagraphStyle(
        "HandbookChapter", parent=body, fontName="HandbookSemibold", fontSize=23, leading=28, spaceAfter=22
    )
    cover = ParagraphStyle("HandbookCover", parent=chapter)
    heading = ParagraphStyle(
        "HandbookSection",
        parent=body,
        fontName="HandbookSemibold",
        fontSize=14,
        leading=19,
        spaceBefore=13,
        spaceAfter=8,
    )
    code_style = ParagraphStyle(
        "HandbookCode",
        parent=styles["Code"],
        fontSize=7.5,
        leading=10,
        backColor=colors.HexColor("#efe8d8"),
        borderPadding=8,
        spaceAfter=14,
    )
    small = ParagraphStyle(
        "HandbookSmall", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#756f5f")
    )

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(PAPER)
        canvas.rect(0, 0, *A4, fill=1, stroke=0)
        canvas.setFillColor(ORANGE)
        canvas.rect(46, A4[1] - 44, 32, 3, fill=1, stroke=0)
        canvas.setFont("HandbookRegular", 8)
        canvas.setFillColor(INK)
        canvas.drawString(46, 28, "ATP | Handbuch | " + revision[:12])
        canvas.drawRightString(A4[0] - 46, 28, str(doc.page))
        canvas.restoreState()

    story = [Spacer(1, 45)]
    logo = assets / "atp-logo.png"
    if logo.exists():
        from reportlab.platypus import Image

        image = Image(str(logo))
        image.drawHeight *= 110 / image.drawWidth
        image.drawWidth = 110
        image.hAlign = "LEFT"
        story.extend([image, Spacer(1, 30)])
    story += [
        Paragraph("Gespräche vorbereiten.<br/>Die Anwendung sicher übergeben.", cover),
        Paragraph("Handbuch für Nutzung, Betrieb und Entwicklung", heading),
        Paragraph("Stand der Textquellen: 15. Oktober 2026", body),
        Paragraph("Git-Version: " + escape(revision), small),
        Paragraph("Quellenhash: " + digest, small),
        Paragraph(
            "Diese Ausgabe beschreibt Quellcode und Anleitungen. Livebetrieb, vollständiger Restore und unabhängige Übernahme benötigen eigene Nachweise.",
            body,
        ),
        PageBreak(),
        Paragraph("Inhalt", heading),
    ]
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("HandbookToc", parent=body, fontSize=11, leading=18, spaceBefore=10)]
    story.extend([toc, PageBreak()])
    for index, file in enumerate(files):
        if index:
            story.append(PageBreak())
        paragraph, code = [], None

        def flush(paragraph=paragraph):
            if paragraph:
                story.append(Paragraph(inline(" ".join(paragraph)), body))
                paragraph.clear()

        for line in file.read_text("utf-8").splitlines():
            if line.startswith("```"):
                flush()
                if code is None:
                    code = []
                else:
                    wrapped = "\n".join(
                        textwrap.fill(part, width=95, replace_whitespace=False, drop_whitespace=False)
                        for part in code
                    )
                    story.append(Preformatted(wrapped, code_style))
                    code = None
            elif code is not None:
                code.append(line)
            elif line.startswith("# "):
                flush()
                story.append(Paragraph(inline(line[2:]), chapter))
            elif line.startswith("## "):
                flush()
                story.append(Paragraph(inline(line[3:]), heading))
            elif line.startswith("- ") or re.match(r"^\d+\. ", line):
                flush()
                story.append(Paragraph(inline(line), body))
            elif not line.strip():
                flush()
            else:
                paragraph.append(line)
        flush()
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = HandbookDoc(
        str(output),
        pagesize=A4,
        leftMargin=46,
        rightMargin=46,
        topMargin=65,
        bottomMargin=50,
        title="ATP Handbuch",
        author="ATP",
    )
    doc.multiBuild(story, onFirstPage=page, onLaterPages=page)
    metadata = {
        "git_revision": revision,
        "sources": source_hashes,
        "source_hash": digest,
        "pdf_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), "utf-8"
    )
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=APP_ROOT / "output/handbook/ATP-Handbuch.pdf")
    parser.add_argument("--revision", default=None)
    args = parser.parse_args()
    result = build_handbook(args.output, revision=args.revision)
    print(json.dumps({"output": str(args.output), "source_hash": result["source_hash"]}))


if __name__ == "__main__":
    main()
