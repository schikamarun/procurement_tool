# Beschaffungs-Bewertungstool

Ein webbasiertes Werkzeug zur Bewertung von Angeboten in öffentlichen Beschaffungsprojekten. Die Anwendung ermöglicht Mehrbenutzerbetrieb mit Rollen, Kriteriensätzen, Angebotsbewertung, Historisierung sowie Import- und Exportfunktionen für Excel-Dateien.

## Funktionen

- Benutzerverwaltung mit Rollen **Admin**, **Evaluator** und **Viewer**
- Projektverwaltung inklusive Preis-/Qualitätsgewichten und Bewertungsstatus
- Kriterien- und Angebotsverwaltung mit optionalem Excel-Import (pandas + openpyxl)
- Zuordnung von Bewertern zu Projekten
- Bewertungsmaske für Evaluatoren mit Pflichtkommentaren, MUSS-Flags und Änderungsverlauf
- Aggregierte Ranglisten mit Qualitäts- und Preisscores sowie MUSS-Verstößen
- Excel-Exporte für Rangliste und Detailbewertungen
- Passwort-Hashing via `bcrypt`

## Installation

1. Python 3.10 oder neuer installieren.
2. Abhängigkeiten installieren:

   ```bash
   pip install -r requirements.txt
   ```

## Starten der Anwendung

```bash
python app.py
```

Nach dem Start steht eine Gradio-Weboberfläche zur Verfügung (Standard: http://127.0.0.1:7860). Ein Standard-Admin-Benutzer wird automatisch angelegt:

- Benutzername: `admin`
- Passwort: `admin`

Es wird eine SQLite-Datenbank (`procurement.db`) im Projektverzeichnis angelegt.

## Excel-Formate

### Kriterienimport

- Arbeitsblattname: `Kriterien`
- Spalten: `Code`, `Titel`, `Beschreibung`, `Kategorie`, `MUSS`, `Gewicht`

### Angebotsimport

- Arbeitsblattname: `Angebote`
- Spalten: `Firma`, `Angebotsname`, `Preis`, `Preis-Kommentar`

## Tests

Automatisierte Tests sind in diesem Prototyp nicht enthalten. Nutzen Sie manuelle Tests über die Weboberfläche, um Workflows zu verifizieren.
