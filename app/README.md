# ATP-Anwendung

Der aktuelle Projekteinstieg steht in der [README im Projektstamm](../README.md). Maßgebliche Textquellen für Bedienung und Betrieb sind das [Handbuch](docs/handbook/00-einstieg.md) und der [Planabgleich](docs/parallel-clarification/PLAN-ABGLEICH.md). Diese Seite beschreibt lokale Entwicklungsbefehle, keine bestätigte aktive Cloudkonfiguration.

## Regulärer Start einer eingerichteten lokalen Testkopie

Aus `app`, in zwei Terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn atp.web:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m atp.worker
```

Vorher die isolierte Testkonfiguration prüfen. Die lokale ATP-Testumgebung verwendet API-Port 55431 und Datenbankport 55432. Fixture- und Live-Worker dürfen keine gemeinsame Queue verwenden. Die [Ersteinrichtung](docs/handbook/03-entwicklung.md) ist vom regulären Start getrennt; `setup_local.ps1` schreibt lokale Konfiguration und ist kein Hosted-Startweg.

## Prüfungen

Aus einer isolierten lokalen Testkopie kann eine gezielte Prüfung ohne echte Recherche laufen:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_rules.py tests/test_clarification_domain.py tests/test_briefing_content.py -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
```

Für die integrierte lokale Suite bei parallel erhaltenem Cloudstand dient im vollständigen Repository der geprüfte Runner:

```powershell
.\.venv\Scripts\python.exe scripts/run_parallel_checks.py --local-config PFAD_ZUR_ISOLIERTEN_ENV_LOCAL -q
```

Der Runner prüft lokale Endpunkte und Fixture-Modus, verwendet pro Arbeitsverzeichnis eine getrennte Queue und liest benötigte synthetische Testzugänge aus der bereits eingerichteten lokalen Testumgebung. Er erzeugt keine neue Datenbank. Echte Modell- oder Hosted-Abnahmen sind gesonderte Schritte und keine Eigenschaft dieses Runners.

`export_package.py` exportiert derzeit einen ausgewählten portablen Testsatz; `run_parallel_checks.py`, historische Cloudhilfen und vollständige Suite sind nicht im Paket. Ein dort fehlender Runner ist kein Installationsfehler, sondern eine dokumentierte Liefergrenze.

## Verträge und Nachweise

- [Architektur](docs/ARCHITEKTUR.md), [Schnittstellen](docs/SCHNITTSTELLEN.md), [Datenbank](docs/DATENBANK.md)
- [Recherche](docs/RECHERCHE.md), [Datenflüsse und Löschung](docs/DATENFLUESSE.md), [Profilverwaltung](docs/PROFIL-UND-VERWALTUNG.md)
- [Historischer Hosted-Betrieb](docs/HETZNER-BETRIEB.md) und [historische Cloud-Anbindung](docs/CLOUD-ANBINDUNG.md)
- [Planabgleich mit geprüften und offenen Teilen](docs/parallel-clarification/PLAN-ABGLEICH.md)

Zugangsdaten, `.env`, echte Briefings und generierte Nachweise bleiben außerhalb von Git. Die Anmeldung bei einem Entwicklungswerkzeug ersetzt keinen API-Schlüssel der Anwendung. Interne Wissens-/Projektfunktionen benötigen eigene Freigaben und Rechte; öffentliche Integrationen bleiben deaktiviert.
