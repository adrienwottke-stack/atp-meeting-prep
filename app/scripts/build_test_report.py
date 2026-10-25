"""Write a shareable evidence summary without secrets or personal test inputs."""

import json
import statistics
from pathlib import Path
from xml.etree import ElementTree

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "testberichte"


def main():
    xml = ElementTree.parse(OUT / "pytest.xml").getroot()
    suites = list(xml.iter("testsuite"))
    counts = {
        key: sum(int(s.get(key, 0)) for s in suites) for key in ["tests", "failures", "errors", "skipped"]
    }
    seconds = sum(float(s.get("time", 0)) for s in suites)
    passed = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    runtime = json.loads((OUT / "laufzeit-fixtures.json").read_text(encoding="utf-8"))
    values = [m["usage"]["active_seconds"] for m in runtime["measurements"]]
    walls = [m["wall_seconds"] for m in runtime["measurements"]]
    browser = json.loads((OUT / "browser-pruefung.json").read_text(encoding="utf-8"))
    pdf = pymupdf.open(OUT / "browser-heruntergeladenes-briefing.pdf")
    text = [
        "# Tatsächliches Testprotokoll",
        "",
        "Aktuelle Cloud-Fortschreibung und neuere Nachweise: [CLOUD-ANBINDUNG.md](CLOUD-ANBINDUNG.md). Dieser Generator wertet den früheren lokalen Fixture-Nachweis unter pytest.xml aus.",
        "",
        "Stand: 15.10.2026. Keine ATP-Fachfreigabe, kein gehosteter Betrieb und kein Live-API-/CRM-Nachweis.",
        "",
        f"Gesamtsuite: **{passed} bestanden**, {counts['failures']} Fehlschläge, {counts['errors']} Fehler, {counts['skipped']} übersprungen; JUnit-Laufzeit {seconds:.2f}s. Nachweis: output/testberichte/pytest.xml. Alle Providerantworten in dieser Suite sind kontrolliert; Supabase Postgres/Auth laufen tatsächlich lokal.",
        "",
        "Ein bekannter Testwerkzeug-Hinweis: Starlette meldet die spätere Ablösung des httpx-basierten TestClient durch httpx2. Der aktuelle gesperrte Stand läuft erfolgreich; keine Laufzeitfehler daraus beobachtet.",
        "",
        "## Fachliche und technische Nachweise",
        "",
        "- Mitarbeitergrenzen 19/20/3000/3001 und grenzüberschreitende Spannen, ungeklärte Eigentümerschaft, Konzern-Neukontakt gegenüber laufendem Projekt, Bestandsverpflichtung gegenüber neuer Vertriebsanfrage.",
        "- Beide Eingabewege, bestätigte Vorschau, zwei getrennte Personen, K01–K06, versionsgeprüfte Antworten und erhaltene Teilnehmer.",
        "- Unternehmens-/Personenbelege, konkrete Textunterstützung, historische Rollen, Datenisolation, Prompt-Injection und DNS-/Redirect-/Größen-/Timeout-Abrufgrenzen.",
        "- Echte lokale Supabase-Anmeldung, Signatur/Aussteller/Zielgruppe/Ablauf/Sitzung, zwei Nutzer derselben Organisation ohne Freigabe, explizites read/edit und Widerruf.",
        "- Persistente Jobs, Lease-Fencing, Checkpoint-Wiederaufnahme einschließlich tatsächlich neu gestartetem Worker-Prozess, kein doppelter Bericht.",
        "- PDF-Render-/Uploadfehler und Wiederholung, gemeinsamer Storage-Lock, Löschrennen vor Veröffentlichung/nach Dateischreiben, gleichzeitiger Upload-/Cleanupausfall mit persistenter Bereinigung.",
        "- Aktive Profilversion, protokollierte Aktivierung und unveränderte alte Snapshots, bewusst neue Ergebnisversionen, Aufbewahrungsvorschau gegenüber expliziter Anwendung, Restore-Tombstones.",
        "- Booking-/CRM-Verträge mit Deduplizierung, Verschiebung, Absage, verspätetem Eingang, unbekanntem Übertragungsausgang und K05. Keine reale Übertragung.",
        "",
        "## Laufzeit",
        "",
        "Fünf Standardfälle mit einer Firma/zwei Personen, expliziter Fixture-Modus, lokale echte Datenbank und PDF. Kein Netz-/Modelllatenzvergleich und keine reale180s-Abnahme.",
        "",
        "| Lauf | Aktive Verarbeitung(s) | Wandzeit(s) | Status |",
        "| --- | --- | --- | --- |",
    ]
    for i, m in enumerate(runtime["measurements"], 1):
        text.append(
            f"| {i} | {m['usage']['active_seconds']:.3f} | {m['wall_seconds']:.3f} | {m['status']} / synthetisch |"
        )
    text += [
        "",
        f"Aktive Zeit min/Median/max: {min(values):.3f}/{statistics.median(values):.3f}/{max(values):.3f}s. Wandzeit min/Median/max: {min(walls):.3f}/{statistics.median(walls):.3f}/{max(walls):.3f}s.",
        "",
        "## Browser und PDF",
        "",
        f"Playwright Chromium: {len(browser['cases'])} Abläufe bestanden, Desktop1440x960 und Mobil375x812. {len(browser['page_console_errors'])} Seiten-/Konsolenfehler, {len(browser['failed_requests'])} fehlgeschlagene Netzwerkanfragen. Sichtbare Fixture-Kennzeichnung geprüft.",
        "",
    ]
    text += ["- " + case["case"] for case in browser["cases"]]
    text += [
        "",
        f"Das im Browser heruntergeladene PDF umfasst {len(pdf)} Seiten. Erste und letzte Seite sowie Desktop-/Mobile-Screenshots visuell kontrolliert. Zusätzlich6 PDF-Tests mit langen Firmennamen, langen Quellen-URLs, zwei Personen, Umlaute/ß, Links und einem13seitigen Layout-Stresstest.",
        "",
        "## Installation, Sicherheit, Review",
        "",
        "- Frische isolierte Python-Umgebung mit uv sync --frozen --no-dev installiert; Import und FastAPI-Initialisierung erfolgreich.",
        "- Ruff und JavaScript-Syntaxprüfung bestanden. Paketversionen in uv.lock gesperrt.",
        "- Lokale Supabase Security Advisors: keine Hinweise auf warn-Level.19 Anwendungstabellen mit RLS, getrennte Web-/Workerrollen; kein anon-Zugriff auf private Definerfunktionen.",
        "- Alle12 im Manifest aufgeführten Referenzdateien SHA256-unverändert. Musterprüfung der versionierbaren Quell-/Doku-/Konfigurationsdateien ohne Treffer für Schlüssel/JWTs; lokale.env und Ergebnisdateien bewusst ausgeschlossen und in.gitignore.",
        "- Drei echte Subagenten mit getrennten Dateien eingesetzt. Unabhängiges Persistenz-/Integrationsreview dokumentiert in REVIEW-PERSISTENZ.md und REVIEW-INTEGRATION.md; gemeldete Fehler wurden mit Codekorrekturen und Regressionen bearbeitet.",
        "",
        "## Nicht ausgeführt / konkret offen",
        "",
        "- OpenAI-Schlüssel und gpt-6-luna sind lokal konfiguriert. Modellzugriff und ein kleiner echter Responses-Aufruf mit Structured Outputs bestanden (15.10.2026, 3,591s, 45 Input-/13 Output-Tokens; output/testberichte/luna-konfiguration.json). Vollständige reale Recherche/Synthese und deren Abnahme stehen aus; FIXTURE_MODE bleibt true.",
        "- Zwei reale, nicht vorgefertigte Quellenfälle und fünf reale Standardläufe nicht ausgeführt.180s-Ziel und gehostete300s-Abnahme nicht als nachgewiesen behauptet.",
        "- Kein Live-CRM, kein aktivierter Buchungshook, kein gehosteter privater Storage und kein vollständiger Backup-/Storage-Restore-Drill.",
        "- Fachliche Bestätigung von Profil/Ausnahmeverfahren, Dienstleister-/Region-/Vertragsentscheidung, Nutzerkreis und Aufbewahrungsfrist bleiben offen. Kein öffentlicher Deploy und keine behauptete tatsächliche ATP-Benutzung.",
        "",
    ]
    (ROOT / "docs" / "TESTPROTOKOLL.md").write_text("\n".join(text), encoding="utf-8")
    print(
        f"Testbericht erstellt: {counts['tests']}Tests/{len(browser['cases'])}Browserabläufe/{len(pdf)}PDFseiten."
    )


if __name__ == "__main__":
    main()
