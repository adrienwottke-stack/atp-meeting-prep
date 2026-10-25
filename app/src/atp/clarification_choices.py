"""Canonical choices for clarification fields backed by fixed input values."""

RELATIONSHIPS = {
    "Neukontakt": "new_contact",
    "Bestandskunde": "existing_customer",
    "Laufendes Projekt": "ongoing_project",
    "Neukontakt; Anlass korrigieren": "new_contact",
}
RELATIONSHIP_CONTEXTS = {
    "Bestehende Verpflichtung": "existing_obligation",
    "Neue Vertriebsanfrage": "new_sales_inquiry",
}
MEETING_TYPES = {
    "Setup-Meeting": "setup",
    "Audit-Beratungsgespräch": "audit_consultation",
    "Enablement-Beratungsgespräch": "enablement_consultation",
    "Projektbesprechung": "project_review",
}
FIXED_CHOICES = {
    "relationship": (list(RELATIONSHIPS)[:3], RELATIONSHIPS),
    "relationship_context": (list(RELATIONSHIP_CONTEXTS), RELATIONSHIP_CONTEXTS),
    "meeting_type": (list(MEETING_TYPES), MEETING_TYPES),
}
FIXED_QUESTIONS = {
    "relationship": (
        "Ist dies ein Neukontakt, ein Bestandskunde oder ein laufendes Projekt?",
        "Die Kundenbeziehung beeinflusst Ton, Erwartungen und Gesprächsansatz.",
    ),
    "relationship_context": (
        "Geht es um eine bestehende Verpflichtung oder eine neue Vertriebsanfrage?",
        "Bestehende Verpflichtungen und neue Vertriebsanfragen erfordern unterschiedliche Empfehlungen.",
    ),
    "meeting_type": (
        "Was ist der bestätigte Anlass des Gesprächs?",
        "Der Gesprächstyp bestimmt die Vorbereitung.",
    ),
}
