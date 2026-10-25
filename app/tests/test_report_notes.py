"""Readable notes retain evidence gaps and raw audit data."""

from copy import deepcopy

import pymupdf
from test_pdf import sample_result

from atp.pdf import render_pdf
from atp.report_notes import quality_notes, report_notes, resolution_notes


def test_person_duplicates_and_rejected_icp_candidate_do_not_flood_report():
    raw = {
        "people": [{"name": "Lea Sommer"}],
        "gaps": [
            "Für Lea Sommer enthalten die bereitgestellten Quellen keinen belegten beruflichen Eintrag.",
            "Lea Sommer: aktuelle berufliche Rolle nicht eindeutig mit Zeitbezug bestätigt.",
            "Lea Sommer: Aktuelle Rolle oder eindeutige Identität offen.",
            "Lea Sommer: Beruflicher Werdegang nicht ausreichend belegt.",
            "Lea Sommer: Relevante berufliche Themen nicht ausreichend belegt.",
            "is_corporation: Aussagewert wird von der Fundstelle nicht konkret gestützt.",
            "Konzernzugehörigkeit: Kein eindeutiger belegter Nachweis für die angefragte Unternehmenseinheit.",
            "locations: kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen.",
            "Lea Sommer: Zwei belegte Rollen widersprechen sich.",
        ],
        "icp": {"criteria": [{"field": "is_corporation", "status": "unclear"}]},
    }
    before = deepcopy(raw)
    notes = report_notes(raw)
    assert raw == before
    assert len(notes["gaps"]) == 4
    assert "Werdegang" in notes["gaps"][0] and "Themen" in notes["gaps"][0]
    assert "is_corporation" not in str(notes) and "locations" not in str(notes)
    assert any("Konzernzugehörigkeit" in gap for gap in notes["gaps"])
    assert any("Zwei belegte Rollen widersprechen" in gap for gap in notes["gaps"])


def test_blocked_urls_group_by_provider_without_publishing_unrelated_person_names():
    raw = {
        "limits": [
            "Beleg nicht nutzbar (evidence_http_999): https://de.linkedin.com/in/lea?utm_source=openai",
            "Beleg nicht nutzbar (evidence_http_999): https://www.linkedin.com/in/unrelated-person",
            "Beleg nicht nutzbar (evidence_http_403): https://www.example.org/private",
            "Faktenextraktion wegen Providerfehler begrenzt.",
        ]
    }
    notes = report_notes(raw)
    assert len(notes["limits"]) == 3
    assert "LinkedIn" in notes["limits"][0]
    assert "unrelated-person" not in str(notes) and "evidence_http" not in str(notes)
    assert notes["limits"][-1] == raw["limits"][-1]


def test_rejected_candidate_is_no_longer_gap_when_final_criterion_is_verified():
    raw = {
        "gaps": ["is_corporation: Aussagewert wird von der Fundstelle nicht konkret gestützt."],
        "icp": {"criteria": [{"field": "is_corporation", "status": "deviating"}]},
    }
    assert report_notes(raw)["gaps"] == []


def test_pdf_uses_same_notes_without_mutating_canonical_diagnostics():
    result = sample_result()
    result.gaps = ["locations: kein abrufbarer konkreter Beleg; nicht als Tatsache übernommen."]
    result.limits = ["Beleg nicht nutzbar (evidence_http_999): https://de.linkedin.com/in/unrelated-person"]
    before = result.model_dump(mode="json")
    doc = pymupdf.open(stream=render_pdf(result), filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert "Standorte: Keine ausreichend belegte Angabe" in text
    assert "LinkedIn: Automatischer Quellenabruf" in text
    assert "evidence_http" not in text and "unrelated-person" not in text
    assert result.model_dump(mode="json") == before


def test_quality_is_persisted_not_inferred_from_technical_completion():
    assert quality_notes({"status": "completed"}) == ("Fachliche Verwendbarkeit nicht bewertet", [])
    assert quality_notes({"quality": {"state": "limited", "reasons": ["Rolle offen."]}}) == (
        "Mit Einschränkungen verwendbar",
        ["Rolle offen."],
    )


def test_resolution_provenance_never_turns_unknown_into_a_confirmed_fact():
    raw = {
        "resolutions": {
            "q1": {
                "question": "Wie ist das Ziel?",
                "action": "provide",
                "value": "Prozessprioritäten",
                "confirmed_by": "test-user",
                "confirmed_at": "2026-10-15T10:00:00Z",
            },
            "q2": {"field": "employees_total", "action": "leave_unknown", "value": ""},
            "q3": "Alte Antwort",
        }
    }
    before = deepcopy(raw)
    notes = resolution_notes(raw)
    assert "Bestätigte Nutzereingabe; kein Web-Beleg" in notes[0]
    assert "test-user" in notes[0] and "2026-10-15" in notes[0]
    assert notes[1] == "Gesamtmitarbeitende: Bewusst ungeklärt gelassen; keine Tatsachenbestätigung."
    assert "Herkunft nicht dokumentiert" in notes[2]
    assert raw == before
