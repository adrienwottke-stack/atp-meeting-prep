"""Generate the complete requirement/acceptance inventory from original references."""

import json
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT.parent / "docs" / "ATP-Anforderungskatalog-Meeting-Vorbereitungs-Agent-v0.3.json"

MODULES = {
    "E01": (
        "models.py, rules.clarify_input, web.check_input",
        "test_rules::test_t02_input_core_and_invalid_urls; E2E T02/T03",
    ),
    "E02": ("MeetingInput.people, PersonProfile, PDF/UI", "E2E T01/T14; K03-Teilnehmerregressionen"),
    "E03": (
        "MeetingType, QUESTION_MATRIX, Storyline",
        "test_providers::test_t01_t04_f01_complete_six_sections_per_anlass",
    ),
    "E04": ("Provider.parse, drafts, Formular/Freitext-Vorschau", "E2E Freitext; scripts/browser_check.py"),
    "E05": ("rules.py K01–K06, versionierte answers", "Regel-, Provider-, CAS- und E2E-Klärungstests"),
    "E06": (
        "Relationship, relationship_context, bestätigter Snapshot",
        "test_existing_obligation_vs_new_sales_vs_unspecified; Browser Version2",
    ),
    "R01": (
        "Provider.research, EvidenceFetcher, Unternehmensfakten",
        "Provider-/Belegtests; F01-E2E; reale Recherche offen",
    ),
    "R02": (
        "Identitätsprüfung und getrennte berufliche Profile",
        "Provider Identität/Zeitbezug/Privatheit; F01-E2E",
    ),
    "R03": (
        "Profilregister, active_profile, unveränderliche runs.profile",
        "test_active_profile_required; E2E T10-Aktivierung",
    ),
    "R04": (
        "OpenAI Responses-Adapter, explizite Fixture-Abgrenzung",
        "SDK2.54.0/Vertragstests; Luna-Modellzugriff und Responses/Structured-Outputs-Smoke live geprüft",
    ),
    "R05": (
        "Source/Fact, konkrete Quote-/Wert-/Einheitsprüfung",
        "test_r05_value_and_concrete_quote_required; SSRF-Tests",
    ),
    "R06": (
        "Versionierte JSON-Dateien, Admin-Aktivierung + DB-Audit",
        "Profilgleichheit mit Katalog; E2E T10 Aktivierung/Snapshots",
    ),
    "A01": (
        "BriefingResult.summary/company, höchstens5 Punkte",
        "Provider F01 vier Anlässe; E2E/PDF/Browser",
    ),
    "A02": (
        "PersonProfile nach participant_id mit Status/Lücken",
        "F01 zwei Personen; K03-Skip/Kandidaten; Browser/PDF",
    ),
    "A03": (
        "Opportunity, service_id, belegte Beobachtung/Hypothese",
        "Provider Referenzprüfung; F01/E2E; PDF",
    ),
    "A04": ("Mindestens5 eindeutige Questions, Anlassmatrix", "Vier Meetingtypen; Pydantic/PDF/Browser"),
    "A05": ("Vier eindeutige StoryPhase mit Ziel/Impuls", "Vier Meetingtypen; Pydantic/PDF/Browser"),
    "A06": (
        "Kanonisches gespeichertes BriefingResult; render_pdf",
        "E2E Inhalt/Download; PDF-Fehler/Retry; Browser",
    ),
    "Q01": ("Firma+berufliches Merkmal, historische Rollen, K03", "T03-/T11-/Identitätsprovider-Tests"),
    "Q02": (
        "Unknown/Lücken/Teilergebnis ohne Fixture-Fallback",
        "Unbekannte Fixture-Firma; Extraktionsfehler; kontrollierter Teilbericht",
    ),
    "Q03": (
        "Persistente Jobs/Leases/CAS/Checkpoints, idempotentes Ergebnis",
        "DB-Neustartsimulation; E2E Checkpoint-/Synthese-/PDF-/Storagefehler",
    ),
    "Q04": (
        "ResearchQuery-Isolation, werkzeuglose Synthese, Escaping",
        "T13-Injection; SSRF; HTML-/PDF-Escaping; Browser",
    ),
    "Q05": (
        "Budget/aktive Zeit, Deadline, Retry-/Suchreservierung",
        "Budgettests; fünf lokale Fixturelaufzeiten; fünf reale Läufe offen",
    ),
    "U01": (
        "Deutschsprachiger Eingabe-/Status-/Rückfragenablauf",
        "E2E K02/CAS; Browser Formular/Freitext; UI-Bestandskundenregression",
    ),
    "U02": (
        "Zusammenfassung/ICP/Empfehlung vor A01–A06, Quellenanker",
        "Desktop/Mobil-Screenshots; PDF-Renderprüfung",
    ),
    "D01": (
        "Meetings/Teilnehmer/Runs/Belege/Entscheidungen verknüpft",
        "Zwei Meetings, Snapshots, E2E Versionierung",
    ),
    "D02": ("Historie/Firmensuche/Termin, bewusster neuer Run", "E2E Version1/2; Browser Historie/Suche"),
    "D03": (
        "Sofortsperre, Tombstones, purge, Storage-Lock/Cleanup, Fristbefehl",
        "E2E Löschrennen; Restore-Guard; Cleanup; Aufbewahrungsvorschau/-anwendung",
    ),
    "I01": ("MeetingInput v1 für manuelle/normalisierte Ereignisse", "BookingEvent- und F01-Vertragstests"),
    "I02": (
        "BookingAdapter/CRMAdapter, Beispiele, deaktivierte Hooks",
        "test_integrations.py; keine reale Zielsystemanbindung",
    ),
    "I03": (
        "Pure Ereignisreduktion, Deduplizierung/Revisions-/Absageregeln",
        "Kontrollierte T15-Tests; reale Hookaktivierung ausstehend",
    ),
    "I04": (
        "CRMExport/K05/Idempotenz/Statusabgleich, Outboxschema",
        "Kontrollierte Ziel-/Exportverträge; kein tatsächlicher CRM-Export",
    ),
    "N01": (
        ".env.example, serverseitige Keys, Gitignore/Redaktion",
        "Geheimnis-Musterscan nur versionierbarer Dateien; Login ohne Keyausgabe",
    ),
    "N02": (
        "Supabase Auth ES256/JWKS/Sitzung, Objektprüfung/RLS/Storage",
        "Zwei echte lokale Nutzer; read/edit/revoke; 19 Tabellen mit RLS; Advisor",
    ),
    "N03": (
        "Getrennte Datentypen, store=false, DATENFLUESSE.md",
        "T13; Setupgate REAL_DATA_APPROVED/RETENTION_DAYS; Betriebsentscheidungen offen",
    ),
    "N04": (
        "Lockfile, Einrichtungsskript, Web-/Workerbefehle, Konfigfehler",
        "Isolierte Umgebung/Start; echter lokaler Supabase-Stack; sauberer Importstart",
    ),
    "N05": (
        "Explizite Fixture-/Live-/Betriebsgrenzen in UI/PDF/Doku",
        "Keine automatische Fallback-Recherche; Status-/Testdokumentation",
    ),
    "L01": (
        "README, Architektur, Konfiguration, Migrationen, Profile, Verträge",
        "Übergabeinventar und tatsächliche lokale Start-/Testprotokolle",
    ),
    "C01": ("Versionierte deterministische ICP-Regeln", "T05–T10 Regeln und Profilgleichheit"),
    "C02": (
        "Criterion Wert/Einheit/Quelle/Herkunft/Zeitbezug",
        "Unbelegte/ungeklärte/konzernfremde Fakten; Inhaberbelegtests",
    ),
    "C03": (
        "Aggregation matching/deviating/unclear + Abweichungsart",
        "19/20/3000/3001 und Grenzspannen; Konflikte",
    ),
    "C04": (
        "Python-Empfehlungsreihenfolge inkl Kundenbeziehung/Anlass",
        "Konzern-Neukontakt vs laufendes Projekt vs Bestandskontext",
    ),
    "C05": (
        "Wissensarbeiter getrennt, Leistungseinstieg nach Anlass",
        "T10 25Gesamt/unbekannteWissensarbeiter; keine harte30erICP-Schwelle",
    ),
    "C06": (
        "Decision separat mit Original/Nutzer/Zeit/Abweichungsgrund",
        "E2E Abweichungsgrundpflicht; Browser-Entscheidung; keine Nachricht",
    ),
}

SCENARIOS = {
    "T01": "test_end_to_end::test_T01_free_text_preview_confirm_preserves_fields; test_T01_T14_full_flow_versions_two_users_pdf_decision_delete; Browser",
    "T02": "test_rules::test_t02_input_core_and_invalid_urls; E2E test_T02_missing_core_no_job_and_idempotent_start",
    "T03": "Regel-/Provider-K02/K03-Tests; E2E test_T03_company_clarification_resumes_without_input_loss",
    "T04": "test_providers::test_t01_t04_f01_complete_six_sections_per_anlass (4Anlässe)",
    "T05": "test_rules::test_t05_unknown_leadership_is_not_negative",
    "T06": "test_rules::test_t06_employee_boundaries; test_spans",
    "T07": "test_rules::test_t07_corporation_new_contact_and_exception",
    "T08": "test_rules::test_t08_ongoing_project_despite_corporation",
    "T09": "test_rules::test_t09_career_gap_does_not_block_meeting",
    "T10": "test_rules::test_t10_workforce_not_knowledge_workers; E2E test_T10_profile_activation_preserves_old_run_snapshot",
    "T11": "test_providers::test_t11_historical_and_undated_roles_remain_historical_with_time_explicit",
    "T12": "E2E test_T12_restart_from_research_checkpoint_fences_old_worker; test_T12_synthesis_failure_produces_honest_partial; Retry-/Budgettests",
    "T13": "test_providers::test_t13_internal_context_never_reaches_search_and_separate_synthesis_tools; SSRF/Markup-Tests",
    "T14": "E2E test_T01_T14_full_flow_versions_two_users_pdf_decision_delete; DB-/Löschrennen-/Restoretests",
    "T15": "test_integrations.py; realer CRM-/Buchungstest nicht ausgeführt",
    "T16": "Saubere Installation/Importstart/Browser mit lokalem Supabase; eigener realer API-Fall ohne Schlüssel nicht ausgeführt",
}


def main():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert len(catalog["requirements"]) == 44 and set(MODULES) == {r["id"] for r in catalog["requirements"]}
    xml = ElementTree.parse(ROOT / "output" / "testberichte" / "pytest.xml").getroot()
    suites = list(xml.iter("testsuite"))
    counts = {
        key: sum(int(s.attrib.get(key, 0)) for s in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    text = [
        "# Implementierungsstatus",
        "",
        "Aktuelle Cloud-Fortschreibung und neuere Nachweise: [CLOUD-ANBINDUNG.md](CLOUD-ANBINDUNG.md). Die nachfolgende Tabelle verwendet den früheren lokalen Fixture-Nachweisstand.",
        "",
        "Stand: 15.10.2026. Alle 44 IDs sind unverändert aus dem Katalog übernommen. Entwicklungsbasis, keine ATP-Fachfreigabe. Ergebnisse der kontrollierten Provider sind synthetisch; Datenbank/Auth und Browser laufen tatsächlich lokal. Kein öffentlicher Deploy und kein tatsächlicher CRM-/Buchungsanschluss.",
        "",
        f"Letzte Gesamtsuite: {counts['tests']} Tests, {counts['failures']} Fehlschläge, {counts['errors']} Fehler, {counts['skipped']} übersprungen. Nachweis: output/testberichte/pytest.xml (lokal, nicht versioniert).",
        "",
        "| ID | Anforderung / Priorität | Implementierung | Test/Nachweis | Tatsächlicher Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for requirement in catalog["requirements"]:
        rid = requirement["id"]
        module, test = MODULES[rid]
        status = "Implementiert; kontrolliert/lokal geprüft"
        if rid in ("R01", "R02", "R05"):
            status = "Implementiert, kontrolliert geprüft; zwei reale Quellenfälle offen"
        elif rid == "R04":
            status = "Grundlegender Modell-API-Nachweis mit gpt-6-luna bestanden; vollständige reale Recherche/Synthese offen"
        elif rid == "Q05":
            status = "Budgets kontrolliert geprüft, Fixturezeiten gemessen; reale5erLaufzeitabnahme offen"
        elif rid in ("I03", "I04"):
            status = "P2 vorbereitet und kontrolliert getestet; nicht live"
        elif rid == "N03":
            status = "Technische Trennung geprüft; echte Datenverarbeitung/Verträge/Region offen"
        elif rid in ("D03", "N02"):
            status = "Lokal mit echter DB/Auth geprüft; hosted Storage/Backuprestore und Betriebsfrist offen"
        text.append(
            f"| {rid} | {requirement['title']} / {requirement['priority']} | {module} | {test} | {status} |"
        )
    text += ["", "## T01–T16", "", "| Fall | Testzuordnung | Status |", "| --- | --- | --- |"]
    for case in catalog["acceptance_scenarios"]:
        status = "Kontrolliert/lokal bestanden; reale Ergänzung getrennt"
        if case["id"] == "T16":
            status = "Lokaler Start geprüft; reale API-Abnahme nicht ausgeführt"
        if case["id"] == "T15":
            status = "Adapterverträge bestanden; Live nicht ausgeführt"
        text.append(f"| {case['id']} – {case['title']} | {SCENARIOS[case['id']]} | {status} |")
    text += ["", "## Offene Betriebs- und Fachentscheidungen", ""]
    text += [
        f"- {d['id']}: {d['topic']} – erforderlich für: {d['needed_for']}." for d in catalog["open_decisions"]
    ]
    text += [
        "",
        "## Dokumentierte technische Konkretisierungen",
        "",
        "- C04/E06: relationship_context unterscheidet bestehende Verpflichtung und neue Vertriebsanfrage bei Bestandskunden; unbekannt bleibt unbekannt.",
        "- I04: CRM-/Outbox-Idempotenz enthält meeting_id, da result_version je Meeting beginnt.",
        "- R05/D01: Beleg-/Faktenkennungen sind textuell; UUID ist keine zusätzliche fachliche Pflicht.",
        "- Q05: search_calls zählt konservativ reservierte Obergrenzen. observed_search_calls und unbekannte Versuche werden separat gemessen. SDK/kontr. Tests belegen nicht die reale Kontokonfiguration.",
        "- Q05: Deadline umfasst Anwendungsarbeit; ein Clienttimeout garantiert weder Providerabbruch noch Kostenstopp. Harte gehostete Laufzeitabnahme und Storage-Ausfall-/Netzwerklatenzen bleiben mit echten Läufen zu messen.",
        "- E05/U01: K02/K03-Feldkorrekturen starten aus vorausgefülltem Eingabestand bewusst eine neue Version; die alte Eingabe bleibt als Audit-Snapshot erhalten.",
        "- D03: keine still konfigurierte Aufbewahrungsfrist und kein automatisch installierter Zeitplan. Der Fristbefehl wird nach bestätigter Frist explizit betrieben.",
        "",
    ]
    (ROOT / "docs" / "IMPLEMENTIERUNGSSTATUS.md").write_text("\n".join(text), encoding="utf-8")
    print(f"44 Anforderungen und16Abnahmefälle dokumentiert; Tests={counts}")


if __name__ == "__main__":
    main()
