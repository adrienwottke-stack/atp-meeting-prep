"""Short task-based copy for the screenshot guide; labels match the application."""

STEPS = [
    {
        "id": "eingeben",
        "image": "eingabe",
        "nav": "Eingeben",
        "area": "Neue Vorbereitung",
        "title": "Wer sitzt dir gegenüber?",
        "intro": "Firma, Ansprechpartner und Gesprächsanlass reichen für den nächsten Schritt. Weitere Angaben ergänzt du bei Bedarf in den aufklappbaren Bereichen.",
        "caption": "Neue Vorbereitung: Basisangaben ausfüllen und optional weitere Details ergänzen.",
        "pins": [(1, "input-company"), (2, "input-person")],
        "notes": [
            (
                "Firma eindeutig angeben",
                "Name und öffentliche Website eintragen. Eine bestimmte Tochterfirma oder einen Standort kannst du unter „Weitere Informationen“ ergänzen.",
            ),
            (
                "Gesprächspartner hinzufügen",
                "Mindestens einen Namen angeben. Weitere Personen über „+ Weitere Person“ ergänzen. Rolle und öffentliches Profil findest du unter „Rollen und Profile“.",
            ),
            (
                "Anlass wählen und Angaben prüfen",
                "Meeting-Anlass auswählen. Gesprächsziel, Kundenbeziehung und Termin sind unter „Gesprächsdetails“ optional. Dann „Angaben prüfen“ klicken; die Recherche startet erst nach deiner Bestätigung.",
            ),
        ],
        "tip_title": "Du hast schon eine Mail oder Gesprächsnotiz?",
        "tip": "Wechsle oben zu „Freitext“ und füge den Text ein. „Vorschau aus Freitext erstellen“ übernimmt einen Vorschlag ins Formular. Prüfe die Zuordnung, bevor du startest. Vertrauliche Details gehören in „Interne Notizen“.",
    },
    {
        "id": "starten",
        "image": "vorschau",
        "nav": "Starten",
        "area": "Eingabevorschau",
        "title": "Einmal prüfen. Dann los.",
        "intro": "Hier siehst du, mit welchen Angaben ATP recherchieren wird.",
        "caption": "Eingabevorschau: Angaben prüfen, Freigabe setzen und Briefing vorbereiten.",
        "pins": [(1, "preview-data"), (2, "preview-check"), (3, "preview-start")],
        "notes": [
            (
                "Angaben abgleichen",
                "Stimmen Firma, Website, Personen und Anlass? Über „Angaben bearbeiten“ kannst du sie korrigieren.",
            ),
            (
                "Öffentliche Recherche bestätigen",
                "Setze den Haken erst nach der Prüfung. Interne Notizen bleiben von öffentlichen Suchbegriffen getrennt.",
            ),
            (
                "„Briefing vorbereiten“ klicken",
                "ATP beginnt die Recherche. Du kannst das Fenster schließen und später über die Historie weitergehen.",
            ),
        ],
    },
    {
        "id": "klaeren",
        "image": "rueckfragen",
        "nav": "Rückfragen",
        "area": "Wenn etwas unklar ist",
        "title": "Eine Rückfrage? So geht’s weiter.",
        "intro": "ATP fragt nur nach Angaben zu deinem Termin oder einer mehrdeutigen Zuordnung. Firmendaten recherchiert die Anwendung.",
        "caption": "Rückfrage: eigene Antwort ergänzen oder auswählen und fortsetzen.",
        "pins": [(1, "question"), (2, "answer"), (3, "continue")],
        "notes": [
            (
                "Den offenen Punkt ansehen",
                "Hier stehen nur die nötigen Rückfragen. Ergebnisse und Quellen findest du im fertigen Briefing.",
            ),
            (
                "Ergänzen oder offenlassen",
                "Wähle einen passenden Kandidaten oder ergänze deine Antwort. Wenn du es nicht weißt, markiere – soweit angeboten – „Weiß ich nicht“.",
            ),
            (
                "Antworten bestätigen",
                "Mit „Übernehmen und fortsetzen“ geht es weiter. Unbelegte Firmendaten halten die Erstellung nicht an und bleiben im Briefing als Lücken sichtbar.",
            ),
        ],
        "takeaway": "Du musst nichts erraten. Ein offener Punkt ist eine gute Frage für das Gespräch.",
    },
    {
        "id": "briefing",
        "image": "briefing",
        "nav": "Briefing",
        "area": "Das Ergebnis nutzen",
        "title": "Das Wichtige zuerst.",
        "intro": "Der Überblick bringt Firma, Gegenüber, Einstieg und nächsten Schritt zusammen.",
        "caption": "Briefing: kompakter Kontext, Gesprächseinstieg und drei priorisierte Fragen.",
        "pins": [(2, "overview")],
        "notes": [
            (
                "Verwendbarkeit prüfen",
                "Öffne „Quellen & Recherchegrenzen“. Die Recherchehinweise zeigen, wie belastbar das Ergebnis ist. Entscheidende Unsicherheiten stehen zusätzlich direkt im Überblick.",
            ),
            (
                "Mit dem Überblick anfangen",
                "Lies den Kontext, den Einstiegsvorschlag, die drei Fragen und den nächsten Schritt. Weitere Inhalte stehen unter „Unternehmen & Personen“, „Einordnung“ und „Quellen & Recherchegrenzen“.",
            ),
            (
                "Wichtige Fakten nachprüfen",
                "Die Quellenlinks führen zum Beleg. Prüfe Firma und Datum. Eine Hypothese ist ein Ansatz zum Nachfragen.",
            ),
        ],
        "takeaway": "Fakt: belegte Aussage. Hypothese: im Gespräch prüfen. Offen: noch zu klären.",
    },
    {
        "id": "entscheiden",
        "image": "entscheidung",
        "nav": "Entscheiden",
        "area": "Dein nächster Schritt",
        "title": "Du triffst die Entscheidung.",
        "intro": "ATP gibt eine Empfehlung. Du hältst fest, wie du weitergehen möchtest.",
        "caption": "Deine Entscheidung: auswählen, bei Abweichung begründen und speichern.",
        "pins": [(1, "decision"), (2, "decision-save")],
        "notes": [
            (
                "Entscheidung auswählen",
                "Öffne unten im Briefing „Entscheidung festhalten“ und wähle deinen nächsten Schritt.",
            ),
            (
                "Speichern",
                "Wenn du von der Empfehlung abweichst, ergänze eine Begründung. Klicke auf „Entscheidung speichern“.",
            ),
            (
                "Briefing mitnehmen",
                "„PDF herunterladen“ lädt das Kurzbriefing. HTML und das PDF mit Nachweisen stehen unter „Weitere Aktionen“. Lege Exporte im vorgesehenen geschützten Ablageort ab.",
            ),
        ],
        "takeaway": "Das Speichern versendet keine Nachricht und bucht oder storniert keinen Termin.",
    },
    {
        "id": "wiederfinden",
        "image": "historie",
        "nav": "Wiederfinden",
        "area": "Historie",
        "title": "Später einfach wieder rein.",
        "intro": "Gespeicherte Vorbereitungen und ihre Ergebnisversionen findest du hier.",
        "caption": "Historie: Vorbereitung suchen, Versionen anzeigen und Bericht öffnen.",
        "pins": [(1, "search"), (2, "versions")],
        "notes": [
            (
                "Firma oder Termin suchen",
                "Öffne „Historie“ in der Navigation und grenze die Liste über die Suche ein.",
            ),
            (
                "Den richtigen Stand öffnen",
                "Klappe „Versionen anzeigen“ auf. Jeder Recherchelauf hat einen eigenen Stand; ältere Ergebnisse bleiben historisch.",
            ),
            (
                "Bei Änderungen neu recherchieren",
                "Ändern sich Firma, Website oder Anlass, starte bewusst eine neue Version. Ein alter Bericht aktualisiert sich nicht automatisch.",
            ),
        ],
    },
]

FAQ = [
    (
        "Ich kann mich nicht anmelden.",
        "Öffne die Anwendung und wähle „Anmelden“. Nutze deinen persönlichen Zugang. Bei einem verlorenen Passwort oder fehlenden Zugang hilft dir der Betreiber.",
    ),
    (
        "Der Startknopf ist noch gesperrt.",
        "Prüfe in der Eingabevorschau den Haken zur Verwendung der öffentlichen Angaben. Im Formular müssen Firma, gültige Website, mindestens ein Ansprechpartner und Meeting-Anlass ausgefüllt sein.",
    ),
    (
        "ATP findet eine Person oder Rolle nicht eindeutig.",
        "Ergänze einen bekannten öffentlichen Profil- oder Firmenlink. ATP muss den Inhalt erst prüfen. Wenn die Zuordnung offen bleibt, behandle die Rolle als Gesprächsfrage.",
    ),
    (
        "Das PDF ist noch nicht verfügbar.",
        "Nutze zunächst die Webansicht oder „HTML herunterladen“. Wenn „PDF erneut erstellen“ angeboten wird, kannst du die Erzeugung erneut anstoßen.",
    ),
    (
        "Es erscheint eine Fehlermeldung.",
        "Notiere die Vorgangskennung und den sichtbaren Status für den Betreiber. Bei einem zwischenzeitlich geänderten Stand lade die Seite neu. Teile keine Zugangsdaten oder vertraulichen Gesprächsinhalte in allgemeinen Supportkanälen.",
    ),
]
