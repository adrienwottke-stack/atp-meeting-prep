"""Deterministic offline HTML export of an already saved briefing.

Only the bundled logo is read locally. No model, network, storage or executable
content participates in rendering. The application may reuse HTML_CSP as a header.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .briefing_content import attention_notes, presentation_overview, reader_result
from .models import BriefingResult
from .report_notes import OVERVIEW_LABELS, quality_notes, report_notes

HTML_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; object-src 'none'"
_KINDS = {
    "fact": "Belegte Tatsache",
    "user_confirmed": "Bestätigte Eingabe",
    "hypothesis": "Hypothese",
    "unknown": "Unbekannt",
}
_ICP = {"matching": "ICP passend", "deviating": "ICP abweichend", "unclear": "ICP ungeklärt"}
_PROFILE = {
    "confirmed": "Zuordnung bestätigt",
    "ambiguous": "Zuordnung mehrdeutig",
    "unresolved": "Zuordnung ungeklärt",
}
_TYPES = {
    "setup": "Setup-Meeting",
    "audit_consultation": "Audit-Beratungsgespräch",
    "enablement_consultation": "Enablement-Beratungsgespräch",
    "project_review": "Projektbesprechung",
}
_RELATIONSHIPS = {
    "new_contact": "Neukontakt",
    "existing_customer": "Bestandskunde",
    "ongoing_project": "Laufendes Projekt",
    "unknown": "Beziehung unbekannt",
}
_SERVICES = {
    "entry": "Ersteinschätzung und Setup",
    "audit": "AI Audit",
    "enablement": "AI Enablement",
    "training": "KI Schulungen",
    "workshop": "Workshop für Führungskräfte",
    "keynote": "Impulse und Keynotes",
}
_CSS = """
:root{color-scheme:light;font:16px/1.6 Arial,Helvetica,sans-serif;color:#302b22;background:#faf7f0}
*{box-sizing:border-box}body{margin:0}header,main,footer{max-width:1120px;margin:auto;padding:28px}
header{border-bottom:1px solid #d7cdb8;display:flex;gap:24px;align-items:center}
header img{width:170px;height:auto}header span,.meta{font-size:13px;color:#756f5f}
h1{font-size:clamp(26px,4vw,42px);line-height:1.2}h2{font-size:23px;line-height:1.35}h3{font-size:18px}
h1,h2,h3{overflow-wrap:anywhere}
a{color:#a93c1a;overflow-wrap:anywhere}p,li,dd{overflow-wrap:anywhere;white-space:pre-line}
.flag,.quality{border:1px solid #d7cdb8;background:#efe8d8;padding:16px;margin:20px 0}
.overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:28px 0}
.card{border:1px solid #d7cdb8;padding:22px;background:#fffdf7;min-width:0}.card:first-child{grid-column:1/-1}
.card h2{margin-top:0}.kind{font-size:12px;color:#a93c1a;font-weight:bold}
.source-peek{font-size:13px;color:#756f5f}.source-peek a{display:block;padding:8px 0;min-height:44px}
summary{cursor:pointer;min-height:44px;padding:10px 0}.report-fold{border-top:1px solid #d7cdb8;padding:8px 0}
.report-fold>summary{font-size:17px;font-weight:bold}.report-fold section.detail{border:0}
.layout{display:block!important}nav{position:static!important;display:flex;flex-wrap:wrap;gap:8px 24px;margin-bottom:24px}
.purpose{border-left:3px solid #cf4f27;padding:8px 20px;margin-top:24px}.purpose p{margin:4px 0}
.layout{display:grid;grid-template-columns:190px minmax(0,1fr);gap:32px}nav{align-self:start;position:sticky;top:20px}
nav a{display:block;padding:7px 0}section.detail{border-top:1px solid #d7cdb8;padding:22px 0;scroll-margin-top:20px}
article{margin-bottom:20px}.priority{border-left:3px solid #cf4f27;padding-left:16px;margin-bottom:20px}
dl{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:8px 20px}dd{margin:0}dt{font-weight:bold}
footer{font-size:12px;color:#756f5f;border-top:1px solid #d7cdb8}.source{font-size:14px}
@media(max-width:650px){header,main,footer{padding:20px}.overview,.layout{grid-template-columns:minmax(0,1fr)}nav{position:static;display:flex;flex-wrap:wrap;gap:10px}dl{display:block}dd{margin-bottom:12px}}
@media print{@page{margin:18mm}body{background:white;font-size:10pt}header,main,footer{padding:0;max-width:none}nav{display:none}.layout,.overview{display:block}.card{padding:12px;margin:12px 0}article,.card,.quality{break-inside:avoid}h2,h3{break-after:avoid}.detail{break-inside:auto}a{color:inherit}header img{width:140px}}
@media print{details::details-content{content-visibility:visible}details>*{display:block!important}details{break-inside:auto}.source-peek{display:none}}
main{max-width:960px}.overview{display:block;margin:28px 0}.context{display:grid;grid-template-columns:1.1fr 1fr;gap:24px 40px}.context>.card:first-child{grid-column:auto}
.section-label{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#756f5f;margin:28px 0 18px}
.card{border:0;background:transparent;padding:0;margin:0 0 20px}.card h2{font-size:17px;margin:0 0 8px}.card p{font-size:15px;margin:0;line-height:1.65}
.opener{background:#f2ece0;border-left:3px solid #cf4f27;padding:24px 28px;margin:12px 0 32px}.opener h2{font-size:12px;color:#a93c1a}.opener p{font-size:24px;line-height:1.45;color:#16140e}
.next-step{margin:24px 0 36px}.purpose{border:0!important;padding:0!important}.purpose strong{font-size:12px;color:#756f5f;font-weight:400}
.detail>h2{font-size:23px}.detail li h3{font-size:17px;line-height:1.45}.detail li{padding:12px 0}.detail li details{font-size:12px;color:#756f5f}
.report-fold>summary{font-size:15px;font-weight:400;padding:17px 0}.report-fold[open]>summary{font-weight:bold}
@media(max-width:600px){.context{grid-template-columns:minmax(0,1fr);gap:4px}.opener{padding:20px}.opener p{font-size:21px}}

"""


def _value(value) -> str:
    if value is None or value == "":
        return "Unbekannt"
    if isinstance(value, bool):
        return "Ja" if value else "Nein"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _e(value) -> str:
    return escape(_value(value).replace("\x00", "\ufffd"), quote=True)


def _date(value) -> str:
    if not value:
        return "Nicht angegeben"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y, %H:%M %Z")


def _safe_url(value: str) -> str | None:
    if re.search(r"[\x00-\x20\\]", value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme.lower() in {"http", "https"} and url.hostname and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return None


def render_html(result: BriefingResult) -> str:
    """Return one portable file; all dynamic strings are escaped at insertion."""
    if not isinstance(result, BriefingResult):
        result = BriefingResult.model_validate(result)
    result = reader_result(result)
    canonical = result.model_dump(mode="json")
    notes = report_notes(canonical)
    quality, quality_reasons = quality_notes(canonical)
    source_numbers = {source.source_id: i + 1 for i, source in enumerate(result.sources)}
    fact_map = {fact.fact_id: fact for fact in result.facts}
    logo = base64.b64encode((Path(__file__).parent / "static" / "atp-logo.png").read_bytes()).decode("ascii")

    def p(text, cls=""):
        return f'<p class="{cls}">{_e(text)}</p>'

    def bullets(values, empty="Keine zusätzlichen Angaben hinterlegt."):
        return (
            "<ul>" + "".join("<li>" + _e(value) + "</li>" for value in values) + "</ul>"
            if values
            else p(empty, "meta")
        )

    def refs(evidence_ids, fact_ids=()):
        ids = list(evidence_ids)
        for fact_id in fact_ids:
            if fact_id in fact_map:
                ids.extend(fact_map[fact_id].evidence_ids)
        selected = [id for id in dict.fromkeys(ids) if id in source_numbers]
        if not selected:
            return ""
        links = []
        for id in selected:
            source = result.sources[source_numbers[id] - 1]
            url = _safe_url(source.url)
            links.append(
                f'<a href="{_e(url)}" rel="noopener noreferrer">{_e(source.title)}</a>'
                if url
                else _e(source.title)
            )
            links.append(
                f'<a href="#source-{source_numbers[id]}">Vollständiger Beleg [{source_numbers[id]}]</a>'
            )
        return (
            '<details class="source-peek"><summary>Quellen ansehen</summary>' + "".join(links) + "</details>"
        )

    def claim(item):
        kind = (
            "Empfehlung"
            if getattr(item, "category", None) == "next_step"
            else _KINDS.get(item.kind, "Unbekannt")
        )
        return (
            (p(kind, "kind") if item.kind != "fact" else "")
            + p(item.text.replace(" (Informationsstand unbekannt)", ""))
            + refs(item.evidence_ids, item.fact_ids)
        )

    sections = {}

    def section(id, title, content):
        sections[id] = f'<section class="detail" id="{id}"><h2>{_e(title)}</h2>{content}</section>'
        return ""

    chunks = [
        '<!doctype html><html lang="de"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="' + escape(HTML_CSP, quote=True) + '">',
        "<title>Gesprächsbriefing · " + _e(result.input.company.name) + "</title>",
        "<style>" + _CSS + "</style></head><body>",
        '<header><img alt="AI Transformation Partners" src="data:image/png;base64,'
        + logo
        + '"><span>Gesprächsbriefing · Intern / Vertraulich</span></header><main>',
        "<h1>" + _e(result.input.company.name) + "</h1>",
        p(
            _TYPES.get(result.input.meeting_type, "Anlass ungeklärt")
            + " · "
            + _RELATIONSHIPS[result.input.relationship]
        ),
        p(
            f"Version {result.result_version} · {'Teilergebnis' if result.status == 'partial' else 'Erstellung abgeschlossen'} · Erstellt: {_date(result.generated_at)} · Meeting: {_date(result.input.scheduled_at)}",
            "meta",
        ),
    ]
    if result.input.relationship == "existing_customer":
        chunks.append(
            p(
                "Bestandskunden-Anlass: "
                + {
                    "unspecified": "Noch nicht eingeordnet",
                    "existing_obligation": "Bestehende Verpflichtung",
                    "new_sales_inquiry": "Neue Vertriebsanfrage",
                }.get(result.input.relationship_context, "Noch nicht eingeordnet")
            )
        )
    if result.fixture_mode:
        chunks.append(
            p(
                "TESTMODUS: Kontrollierte synthetische Quellen. Kein Nachweis über ein reales Unternehmen.",
                "flag",
            )
        )
    chunks.append(
        '<div class="purpose"><strong>Dein Gesprächsziel</strong>'
        + p(result.input.goal or "Erwartungen und nächsten Schritt gemeinsam klären.")
        + "</div>"
    )
    if attention_notes(result):
        chunks.append('<aside class="flag">' + bullets(attention_notes(result)) + "</aside>")
    overview = presentation_overview(result)
    next_step = ""
    if overview:
        chunks.append(
            '<section class="overview" aria-label="Briefing auf einen Blick"><h2 class="section-label">Für deinen Termin</h2><div class="context">'
        )
        for item in overview:
            if item.category in ("opener", "next_step"):
                continue
            chunks.append(
                '<article class="card"><h2>'
                + _e(OVERVIEW_LABELS[item.category])
                + "</h2>"
                + claim(item)
                + "</article>"
            )
        chunks.append("</div>")
        for item in overview:
            if item.category == "opener":
                chunks.append(
                    '<article class="card opener"><h2>Dein Gesprächseinstieg · Vorschlag</h2>'
                    + p(re.sub(r"^Gesprächsvorschlag:\s*", "", item.text))
                    + "</article>"
                )
            if item.category == "next_step":
                next_step = (
                    '<article class="card next-step"><h2>Einordnung und nächster Schritt</h2>'
                    + claim(item)
                    + "</article>"
                )
        chunks.append("</section>")
    chunks.append('<div class="layout" aria-label="Briefing-Inhalt"><div>')
    chunks.append(
        section(
            "A01",
            "Unternehmensüberblick",
            "".join(claim(item) for item in result.company)
            or p("Keine hinreichend belegten Unternehmensangaben verfügbar."),
        )
    )
    people = []
    for person in result.people:
        people.append(
            "<article><h3>"
            + _e(person.name)
            + "</h3>"
            + p(_PROFILE[person.status], "meta")
            + "<h4>Rolle</h4>"
            + claim(person.role)
            + "<h4>Beruflicher Werdegang</h4>"
            + (
                "".join(claim(item) for item in person.career)
                or p("Keine belegten Karrieredetails verfügbar.")
            )
            + "<h4>Berufliche Themen</h4>"
            + (
                "".join(claim(item) for item in person.topics)
                or p("Keine belegten beruflichen Themen verfügbar.")
            )
            + "<h4>Offen</h4>"
            + bullets(person.gaps)
            + "</article>"
        )
    chunks.append(section("A02", "Ansprechpartner im Überblick", "".join(people)))
    opportunities = [
        p("Anlass: " + _TYPES.get(result.input.meeting_type, "Anlass ungeklärt")),
        p("Gesprächsziel: " + (result.input.goal or "Nicht zusätzlich angegeben.")),
    ]
    for item in result.opportunities:
        opportunities.append(
            "<article><h3>"
            + _e(_SERVICES.get(item.service_id, item.service_id))
            + "</h3><h4>Beobachtung</h4>"
            + p(item.observation)
            + refs(item.evidence_ids)
            + "<h4>Hypothese · im Gespräch prüfen</h4>"
            + p(item.hypothesis)
            + "<h4>Prüfungsfrage</h4>"
            + p(item.validation_question)
            + "</article>"
        )
    if not result.opportunities:
        opportunities.append(
            p("Kein belastbarer Ansatzpunkt abgeleitet. Die Fragen klären den möglichen Bedarf.")
        )
    chunks.append(section("A03", "Anlass und ATP-Ansatzpunkte", "".join(opportunities)))
    questions = ["<ol>"]
    for index, question in enumerate(result.questions):
        if index == 3:
            questions.append(
                '</ol><details class="report-fold"><summary>Weitere Gesprächsfragen</summary><ol start="4">'
            )
        questions.append(
            '<li class="'
            + ("priority" if index < 3 else "")
            + '"><h3>'
            + _e(question.text)
            + "</h3>"
            + "<details><summary>Warum diese Frage?</summary>"
            + p(question.purpose)
            + p(question.reference, "meta")
            + "</details>"
            + "</li>"
        )
    questions.append("</ol>")
    if len(result.questions) > 3:
        questions.append("</details>")
    # Render this section before the grouped background at the end of the document.
    section("A04", "Fragen für das Gespräch", "".join(questions))
    chunks.append(
        section(
            "A05",
            "Gesprächs-Storyline",
            "".join(
                "<article><h3>"
                + _e(phase.phase)
                + "</h3>"
                + p("Ziel: " + phase.goal)
                + p("Gesprächsimpuls: " + phase.impulse)
                + "</article>"
                for phase in result.storyline
            ),
        )
    )
    icp = [p(_ICP[result.icp.status])]
    icp.append(p(result.recommendation.value + ": " + result.recommendation.reason))
    for criterion in result.icp.criteria:
        icp.append(
            "<article><h3>"
            + _e(
                criterion.label
                + " · "
                + {"matching": "Erfüllt", "deviating": "Abweichend", "unclear": "Ungeklärt"}[criterion.status]
            )
            + "</h3>"
            + p("Wert: " + _value(criterion.value))
            + p(criterion.reason)
            + refs(criterion.evidence_ids)
            + p(
                "Herkunft: "
                + {
                    "web_verified": "Web-Beleg",
                    "user_confirmed": "Bestätigte Eingabe",
                    "unknown": "Unbekannt",
                }[criterion.provenance]
                + " · Bezug: "
                + (result.input.company.name if criterion.subject_ref == "company" else criterion.subject_ref)
                + " · Informationsstand: "
                + (criterion.information_as_of or "Unbekannt"),
                "meta",
            )
            + "</article>"
        )
        if criterion.deviation_kind:
            icp.append(
                p(
                    "Abweichungsart: "
                    + {
                        "corridor": "Größenkorridor",
                        "target_structure": "Zielstruktur",
                        "exclusion": "Bestätigter Ausschluss",
                    }[criterion.deviation_kind],
                    "meta",
                )
            )
    icp.extend(
        [
            "<h3>Passender Leistungseinstieg</h3>",
            p(result.icp.entry_reason or "Noch zu klären."),
            p("Leistungen: " + ", ".join(_SERVICES.get(s, s) for s in result.icp.entry_service_ids)),
            "<h3>Offene Punkte</h3>",
            bullets(result.icp.open_points),
        ]
    )
    chunks.append(section("icp", "Zielgruppenpassung", "".join(icp)))
    sources = [
        '<details class="quality"><summary>Recherchehinweise · '
        + _e(quality)
        + "</summary>"
        + (bullets(list(dict.fromkeys(quality_reasons))) if quality_reasons else "")
        + "</details>"
    ]
    if result.research_coverage:
        sources.append("<h3>Rechercheabdeckung</h3>")
        for coverage in result.research_coverage:
            sources.append(
                p(
                    {
                        "company": "Unternehmen",
                        "developments": "Entwicklungen",
                        "people": "Ansprechpartner",
                        "icp": "Zielgruppenprüfung",
                    }[coverage.area]
                    + ": "
                    + {
                        "covered": "Abgedeckt",
                        "partial": "Teilweise abgedeckt",
                        "missing": "Öffentlich nicht ausreichend belegt",
                    }[coverage.status]
                )
                + bullets(coverage.reasons)
            )
    sources.extend(
        [
            "<h3>Offene Punkte</h3>",
            bullets(notes["gaps"]),
            "<h3>Bestätigte Angaben und offene Entscheidungen</h3>",
            bullets(notes["resolutions"]),
            "<h3>Grenzen dieses Laufs</h3>",
            bullets(notes["limits"]),
            "<h3>Quellenverzeichnis</h3>",
            p(
                "Abrufzeit und Informationsstand sind getrennt angegeben. Ein heutiger Abruf bestätigt keine aktuelle Gültigkeit.",
                "meta",
            ),
        ]
    )
    source_notes = "".join(sources)
    sources = []
    for number, source in enumerate(result.sources, 1):
        url = _safe_url(source.url)
        link = (
            '<a href="' + _e(url) + '" rel="noopener noreferrer">' + _e(source.url) + "</a>"
            if url
            else _e(source.url)
        )
        sources.append(
            f'<article class="source" id="source-{number}"><h3>[{number}] '
            + _e(source.title)
            + "</h3><p>"
            + link
            + "</p>"
            + p(
                f"Abgerufen: {_date(source.retrieved_at)} · Informationsstand: {source.information_as_of or 'Unbekannt'} · Veröffentlicht: {source.published_at or 'Unbekannt'}",
                "meta",
            )
            + p(
                "Beleg geprüft"
                if source.verified
                else "Profiltreffer · Inhalt nicht direkt geprüft"
                if source.retrieval_kind == "search_index"
                else "Nicht hinreichend geprüft",
                "meta",
            )
            + (
                "<details><summary>Textstelle lesen</summary>" + p(source.excerpt) + "</details>"
                if source.excerpt
                else ""
            )
            + (p(source.limitation, "meta") if source.limitation else "")
            + "</article>"
        )
    if not result.sources:
        sources.append(p("Keine prüfbaren Web-Belege verfügbar."))
    sources.append(
        '<details class="report-fold"><summary>Rechercheverlauf und offene Punkte</summary>'
        + source_notes
        + "</details>"
    )
    sources.append(
        "<h3>Ergebnis- und Versionsstand</h3>"
        + p(
            f"Meeting: {result.meeting_id} · Recherchelauf: {result.run_id} · Briefing: {result.briefing_id}",
            "meta",
        )
        + p(
            f"Profil: {result.profile_version} · Profil-Hash: {result.profile_hash} · Prompt: {result.prompt_version} · Regeln: {result.rule_version} · Darstellung: {result.presentation_version}",
            "meta",
        )
        + p("Modelle: " + _value(result.models), "meta")
    )
    chunks.append(section("A06", "Quellen und Wissenslücken", "".join(sources)))
    if result.memory:
        entries = []
        for entry in result.memory.get("entries", []):
            entries.append(
                p(entry.get("text", ""))
                + p(
                    f"{entry.get('context_name', 'Kontext')} · {entry.get('source', 'Bestätigte Eingabe')} · Stand {entry.get('information_as_of') or entry.get('confirmed_at') or 'Unbekannt'} · Version {entry.get('version', 'Unbekannt')}",
                    "meta",
                )
            )
        chunks.append(
            section(
                "knowledge",
                "Berücksichtigtes Wissen",
                "".join(entries) or p("Keine bestätigten Wissenseinträge vorhanden."),
            )
        )
    chunks.append(
        section(
            "decision",
            "Menschliche Entscheidung",
            p(
                "Die Terminempfehlung ist keine automatische Terminaktion. Eine gegebenenfalls später gespeicherte menschliche Entscheidung wird separat in der Anwendung geführt und ist nicht Teil dieses Exports."
            ),
        )
    )

    def fold(title, content):
        return '<details class="report-fold"><summary>' + _e(title) + "</summary>" + content + "</details>"

    chunks.extend(
        [
            sections["A04"],
            next_step,
            '<h2 class="section-label">Bei Bedarf vertiefen</h2>',
            fold("Unternehmen & Personen", sections["A01"] + sections["A02"]),
            fold(
                "Einordnung",
                sections["A03"] + sections["A05"] + sections["icp"] + sections.get("knowledge", ""),
            ),
            fold("Quellen & Recherchegrenzen", sections["A06"]),
            fold("Entscheidung", sections["decision"]),
        ]
    )
    chunks.append(
        "</div></div></main><footer>AI Transformation Partners GmbH · Intern / Vertraulich · Gespeicherter Recherchestand; offline lesbarer Export.</footer></body></html>"
    )
    return "\n".join(chunks)
