"""Preparation UI against local assets and synthetic intercepted APIs only."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_clarification_presentation import browser as browser
from test_clarification_presentation import mount


def start(browser, width=1280, *, memory=False, responses=None):
    defaults = {
        "/v1/memory/status": {"enabled": True},
        "/v1/contexts": {"items": [
            {"id": "context-a", "kind": "company", "name": "Musterwerk"},
            {"id": "context-b", "kind": "project", "name": "Pilot"},
            {"id": "context-c", "kind": "project", "name": "Weitere Initiative"},
        ]},
        "/v1/memory/preview": {"entries": [], "feedback": []},
    }
    page, _, errors, failed = mount(
        browser, {}, "/", width, memory_enabled=memory,
        responses={**defaults, **(responses or {})},
    )
    page.get_by_role("heading", name="Gespräch vorbereiten", exact=True).wait_for()
    return page, errors, failed


def basics(page):
    page.get_by_label("Firmenname", exact=True).fill("Musterwerk GmbH")
    page.get_by_label("Website", exact=True).fill("https://musterwerk.example")
    page.get_by_label("Name", exact=True).first.fill("Lea Sommer")
    page.get_by_label("Meeting-Anlass", exact=True).select_option("setup")


def optional(page, name):
    return page.locator("details.optional-section").filter(
        has=page.locator("summary").filter(has_text=name)
    )


def open_optional(page, name):
    block = optional(page, name)
    if block.get_attribute("open") is None:
        block.locator("summary").click()
    return block


def screenshot(page, tmp_path, name):
    directory = Path(os.environ.get("ATP_UI_ARTIFACTS", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / name), full_page=True)


@pytest.mark.parametrize("width", [1440, 1280, 375])
def test_basis_preview_edit_and_layout(browser, width, tmp_path):
    page, errors, failed = start(browser, width, memory=True)
    requests = []
    page.on("request", lambda r: requests.append((r.method, r.url)))
    expect(page.locator(".optional-section")).to_have_count(4)
    expect(page.locator(".optional-section[open]")).to_have_count(0)
    expect(page.get_by_label("Rolle / Funktion · optional")).not_to_be_visible()
    expect(page.get_by_role("button", name="entfernen", exact=False)).to_have_count(0)
    assert page.locator(".preparation").bounding_box()["width"] <= 920
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    screenshot(page, tmp_path, f"after-{width}.png")
    basics(page)
    page.get_by_role("button", name="Angaben prüfen →").click()
    expect(page.get_by_role("heading", name="Einmal prüfen. Dann recherchieren.")).to_be_visible()
    expect(page.locator('[aria-current="step"]')).to_have_text("02Prüfung")
    expect(page.get_by_role("button", name="Briefing vorbereiten", exact=True)).to_be_disabled()
    expect(page.locator(".preview-row").filter(has_text="Musterwerk GmbH")).to_be_visible()
    screenshot(page, tmp_path, f"preview-{width}.png")
    assert not any(method == "POST" for method, _ in requests)
    page.get_by_role("button", name="← Angaben bearbeiten").click()
    expect(page.get_by_label("Firmenname", exact=True)).to_have_value("Musterwerk GmbH")
    expect(page.locator(".optional-section[open]")).to_have_count(0)
    assert not errors and not failed
    page.close()


def test_optional_values_survive_closed_sections_preview_and_mode_switch(browser, tmp_path):
    page, errors, failed = start(browser)
    basics(page)
    profiles = open_optional(page, "Rollen und Profile")
    page.get_by_label("Rolle / Funktion · optional").fill("Geschäftsführerin")
    page.get_by_label("Öffentliches berufliches Profil · optional").fill("https://example.org/lea")
    page.get_by_role("checkbox", name="Rolle und Profil-URL").check()
    expect(profiles.get_by_role("heading", name="Lea Sommer")).to_be_visible()
    meeting = open_optional(page, "Gesprächsdetails")
    page.get_by_label("Kundenbeziehung", exact=True).select_option("existing_customer")
    page.get_by_label("Anlass beim Bestandskunden").select_option("new_sales_inquiry")
    page.get_by_label("Gesprächsziel · optional").fill("Pilot abstimmen")
    page.get_by_label("Termin · optional").fill("2026-10-20T14:30")
    extra = open_optional(page, "Weitere Informationen")
    page.get_by_label("Gemeinte Unternehmenseinheit · optional").fill("Standort Dresden")
    page.get_by_label("Interne Notizen · optional").fill("Synthetische interne Notiz")
    page.get_by_label("Begründeter Ausnahmefall · optional").fill("Bekannte Abweichung prüfen")
    screenshot(page, tmp_path, "optional-expanded-1280.png")
    for block in (profiles, meeting, extra):
        block.locator("summary").click()
        expect(block.locator("summary")).to_contain_text("Angaben ergänzt")
    page.get_by_role("button", name="Angaben prüfen →").click()
    expect(page.locator(".preview-row").filter(has_text="Geschäftsführerin")).to_be_visible()
    page.get_by_role("button", name="← Angaben bearbeiten").click()
    expect(page.locator(".optional-section[open]")).to_have_count(3)
    page.get_by_role("tab", name="Freitext", exact=True).click()
    page.get_by_label("Was steht an?").fill("Synthetischer Freitextentwurf")
    page.get_by_role("tab", name="Formular", exact=True).click()
    expected = {
        "Rolle / Funktion · optional": "Geschäftsführerin",
        "Öffentliches berufliches Profil · optional": "https://example.org/lea",
        "Gesprächsziel · optional": "Pilot abstimmen",
        "Termin · optional": "2026-10-20T14:30",
        "Gemeinte Unternehmenseinheit · optional": "Standort Dresden",
        "Interne Notizen · optional": "Synthetische interne Notiz",
        "Begründeter Ausnahmefall · optional": "Bekannte Abweichung prüfen",
    }
    for label, value in expected.items():
        expect(page.get_by_label(label, exact=True)).to_have_value(value)
    expect(page.get_by_role("checkbox", name="Rolle und Profil-URL")).to_be_checked()
    expect(page.get_by_label("Anlass beim Bestandskunden")).to_have_value("new_sales_inquiry")
    page.get_by_role("tab", name="Freitext", exact=True).click()
    expect(page.get_by_label("Was steht an?")).to_have_value("Synthetischer Freitextentwurf")
    assert not errors and not failed
    page.close()


def test_people_removal_preserves_identity_and_limit(browser):
    submitted = []

    def submit(request):
        submitted.append(request.post_data_json)
        return {"run_id": "synthetic"}

    page, errors, failed = start(browser, responses={
        "/v1/preparations": submit,
        "/v1/runs/synthetic": {"status": "failed"},
    })
    basics(page)
    for name in ("Tobias Kern", "Mara Wolf"):
        page.get_by_role("button", name="+ Weitere Person", exact=True).click()
        page.get_by_label("Name", exact=True).last.fill(name)
    profiles = open_optional(page, "Rollen und Profile")
    for index, role in enumerate(("Leitung", "Einkauf", "IT")):
        profiles.get_by_label("Rolle / Funktion · optional").nth(index).fill(role)
    profiles.get_by_role("checkbox").nth(2).check()
    # Remember IDs through a preview, without posting or reading module internals.
    page.get_by_role("button", name="Angaben prüfen →").click()
    page.get_by_role("button", name="← Angaben bearbeiten").click()
    page.get_by_role("button", name="Tobias Kern entfernen", exact=True).click()
    expect(page.get_by_label("Name", exact=True)).to_have_count(2)
    for index, name in enumerate(("Lea Sommer", "Mara Wolf")):
        expect(page.get_by_label("Name", exact=True).nth(index)).to_have_value(name)
    profiles = optional(page, "Rollen und Profile")
    for index, role in enumerate(("Leitung", "IT")):
        expect(profiles.get_by_label("Rolle / Funktion · optional").nth(index)).to_have_value(role)
    for _ in range(8):
        page.get_by_role("button", name="+ Weitere Person", exact=True).click()
        page.get_by_label("Name", exact=True).last.fill("Zusatzperson")
    expect(page.get_by_role("button", name="+ Weitere Person", exact=True)).to_be_disabled()
    while page.get_by_label("Name", exact=True).count() > 2:
        page.get_by_role("button", name="Zusatzperson entfernen", exact=True).last.click()
    expect(page.get_by_role("button", name="+ Weitere Person", exact=True)).to_be_enabled()
    page.get_by_role("button", name="Angaben prüfen →").click()
    page.get_by_role("checkbox", name="Ich habe die Angaben geprüft.").check()
    page.get_by_role("button", name="Briefing vorbereiten", exact=True).click()
    # The mocked status is terminal; the initial loading heading can disappear
    # before Playwright observes it when the local response arrives immediately.
    page.get_by_role("heading", name="Die Vorbereitung wurde gestoppt.").wait_for()
    assert len(submitted) == 1
    people = submitted[0]["people"]
    assert [(p["name"], p["role_hint"], p["public_identifiers_confirmed"]) for p in people] == [
        ("Lea Sommer", "Leitung", False), ("Mara Wolf", "IT", True),
    ]
    assert len({p["participant_id"] for p in people}) == 2
    assert not errors and not failed
    page.close()


@pytest.mark.parametrize("field,value,section", [
    ("Website", "keine-url", None),
    ("Öffentliches berufliches Profil · optional", "keine-url", "Rollen und Profile"),
    ("Termin · optional", "2027-03-28T02:30", "Gesprächsdetails"),
])
def test_invalid_values_open_section_and_focus(browser, field, value, section):
    page, errors, failed = start(browser)
    basics(page)
    if section:
        block = open_optional(page, section)
    page.get_by_label(field, exact=True).fill(value)
    if section:
        block.locator("summary").click()
    page.get_by_role("button", name="Angaben prüfen →").click()
    expect(page.get_by_label(field, exact=True)).to_be_focused()
    if section:
        expect(block).to_have_attribute("open", "")
    expect(page.get_by_role("heading", name="Gespräch vorbereiten", exact=True)).to_be_visible()
    assert not errors and not failed
    page.close()


def test_freetext_import_opens_populated_sections(browser):
    preview = {
        "schema_version": 1,
        "company": {"name": "Musterwerk", "website": "https://example.org", "entity_hint": "Dresden"},
        "people": [{"participant_id": "stable-person", "name": "Lea Sommer", "role_hint": "Leitung"}],
        "meeting_type": "setup", "goal": "Pilot besprechen",
    }
    page, errors, failed = start(browser, responses={
        "/v1/input/parse": {"preview": preview, "draft_id": "draft", "warnings": []},
    })
    page.get_by_role("tab", name="Freitext", exact=True).click()
    page.get_by_label("Was steht an?").fill("Synthetische Gesprächsanfrage")
    page.get_by_role("button", name="Vorschau aus Freitext erstellen").click()
    expect(page.get_by_label("Firmenname", exact=True)).to_have_value("Musterwerk")
    expect(page.locator(".optional-section[open]")).to_have_count(3)
    assert not errors and not failed
    page.close()


def test_memory_selection_survives_preview_and_obeys_limit(browser):
    page, errors, failed = start(browser, memory=True)
    basics(page)
    knowledge = open_optional(page, "Gespeichertes Wissen")
    knowledge.get_by_role("checkbox", name="Unternehmen: Musterwerk", exact=True).check()
    knowledge.get_by_role("checkbox", name="Projekt: Pilot", exact=True).check()
    knowledge.get_by_role("checkbox", name="Projekt: Weitere Initiative", exact=True).check()
    expect(page.get_by_role("status")).to_contain_text("höchstens zwei")
    page.get_by_role("button", name="Angaben prüfen →").click()
    page.get_by_role("button", name="← Angaben bearbeiten").click()
    knowledge = optional(page, "Gespeichertes Wissen")
    expect(knowledge).to_have_attribute("open", "")
    expect(knowledge.get_by_role("checkbox", name="Unternehmen: Musterwerk", exact=True)).to_be_checked()
    expect(knowledge.get_by_role("checkbox", name="Projekt: Pilot", exact=True)).to_be_checked()
    expect(knowledge.get_by_role("checkbox", name="Projekt: Weitere Initiative", exact=True)).not_to_be_checked()
    assert not errors and not failed
    page.close()


def test_memory_failure_is_visible_and_basis_still_works(browser):
    page, errors, failed = start(browser, memory=True, responses={
        "/v1/memory/status": (503, {"message": "Wissen vorübergehend nicht erreichbar."}),
    })
    expect(page.get_by_role("alert")).to_contain_text("Wissen vorübergehend nicht erreichbar.")
    expect(optional(page, "Gespeichertes Wissen")).to_have_attribute("open", "")
    basics(page)
    page.get_by_role("button", name="Angaben prüfen →").click()
    expect(page.get_by_role("heading", name="Einmal prüfen. Dann recherchieren.")).to_be_visible()
    assert not [error for error in errors if "503" not in error] and not failed
    page.close()


def test_keyboard_opens_optional_section_and_enters_field(browser):
    page, errors, failed = start(browser, width=375)
    summary = optional(page, "Rollen und Profile").locator("summary")
    summary.focus()
    summary.press("Enter")
    expect(optional(page, "Rollen und Profile")).to_have_attribute("open", "")
    summary.press("Tab")
    expect(page.get_by_label("Rolle / Funktion · optional")).to_be_focused()
    summary.press("Space")
    expect(optional(page, "Rollen und Profile")).not_to_have_attribute("open", "")
    assert not errors and not failed
    page.close()


def test_mobile_expanded_details_and_long_person_names(browser, tmp_path):
    page, errors, failed = start(browser, width=375, memory=True)
    basics(page)
    page.get_by_label("Name", exact=True).fill("Alexandra " + "Langermann" * 12)
    page.get_by_role("button", name="+ Weitere Person", exact=True).click()
    page.get_by_label("Name", exact=True).last.fill("Tobias Kern")
    profiles = open_optional(page, "Rollen und Profile")
    profiles.get_by_label("Rolle / Funktion · optional").first.fill("Geschäftsführung")
    open_optional(page, "Gesprächsdetails")
    page.get_by_label("Kundenbeziehung", exact=True).select_option("existing_customer")
    page.get_by_label("Gesprächsziel · optional").fill("Einen gemeinsamen Pilotversuch abstimmen.")
    open_optional(page, "Weitere Informationen")
    page.get_by_label("Interne Notizen · optional").fill("Synthetischer interner Kontext.")
    knowledge = open_optional(page, "Gespeichertes Wissen")
    knowledge.get_by_role("checkbox", name="Unternehmen: Musterwerk", exact=True).check()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.get_by_role("button", name="+ Weitere Person", exact=True).bounding_box()["height"] >= 44
    screenshot(page, tmp_path, "optional-expanded-375.png")
    page.get_by_role("button", name="Angaben prüfen →").click()
    expect(page.get_by_role("heading", name="Einmal prüfen. Dann recherchieren.")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors and not failed
    page.close()
