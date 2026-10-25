"""Regressions discovered by the real hosted acceptance run."""

import pytest
from test_pdf import sample_result
from test_providers import extraction_fact, meeting, source

from atp.briefing_content import build_overview
from atp.models import Fact, ResearchQuery, Source
from atp.providers import Extraction, _value_supported, validate_extraction
from atp.research_pipeline import extraction_priority


@pytest.mark.parametrize("field", ["services", "business_model"])
@pytest.mark.parametrize("text", [
    "operate this website for its company as well as for affiliated companies",
    "Wir betreiben diese Website für unser Unternehmen und verbundene Unternehmen.",
    "Conditions of use for our customers",
])
def test_website_terms_are_not_company_products_or_services(field, text):
    assert not _value_supported(field, text, "Musterwerk GmbH: " + text)


@pytest.mark.parametrize("text", [
    "Wir entwickeln Websites für Kunden und betreiben deren Onlineshops.",
    "We operate websites for customers and provide web hosting services.",
    "Werkzeugmaschinen und industrielle Lasertechnik",
])
def test_real_customer_offerings_remain_usable(text):
    assert _value_supported("services", text, "Musterwerk GmbH: " + text)


def test_single_source_recovery_prefers_business_content_over_long_terms():
    legal = Source(url="https://musterwerk.example/conditions-of-use", title="Conditions of use",
                   excerpt="Musterwerk operates this website. " * 300)
    business = Source(url="https://reference.example/musterwerk", title="Musterwerk GmbH",
                      excerpt="Musterwerk GmbH entwickelt Produkte und bietet Leistungen für Kunden.")
    query = ResearchQuery.from_input(meeting())
    assert extraction_priority(business, query) > extraction_priority(legal, query)


def test_historical_role_keeps_its_date_and_is_not_presented_as_a_professional_topic():
    result = sample_result()
    person = result.input.people[0]
    result.people[0].status = "confirmed"
    result.facts = [
        *result.facts,
        Fact(subject_ref=person.participant_id, field="undated_role", value="Chief Executive Officer (CEO)",
             status="verified", provenance="web_verified", evidence_ids=["s1"]),
        Fact(subject_ref=person.participant_id, field="historical_role", value="Chief Executive Officer",
             status="historical", provenance="web_verified", information_as_of="2022-10-27", evidence_ids=["s1"]),
    ]
    people = next(item for item in build_overview(result) if item.category == "people")
    assert "Frühere Rolle" in people.text and "2022-10-27" in people.text
    assert "Berufliches Thema" not in people.text


@pytest.mark.parametrize("value,field,accepted", [
    ("Werkzeugmaschinen", "services", True),
    ("Erfundene Produkte", "services", False),
    ("etwa 120", "employees_total", False),
    ("familiengeführt", "owner_or_family_led", False),
])
def test_literal_text_values_are_recovered_only_with_verbatim_evidence(value, field, accepted):
    quote = "Musterwerk GmbH bietet Werkzeugmaschinen und ist familiengeführt mit etwa 120 Mitarbeitenden."
    extracted = Extraction(facts=[extraction_fact(field=field, value_json=value, quote=quote)],
                           ambiguous_participant_ids=[], gaps=[])
    bundle = validate_extraction(extracted, [source(quote)], ResearchQuery.from_input(meeting()))
    assert bool(bundle.facts) is accepted
