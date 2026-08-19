# HGSFP Umfrage-Dashboard

Web-Dashboard für die Feedback-Umfragen der Heidelberg Physics Graduate Days:
Umfragen anlegen und bearbeiten, per Link und QR-Code verteilen, anonym ausfüllen lassen und
auswerten — Vorschau im Browser, Export als PDF.

Die Rechenkerne stammen aus [hgsfp-survey-tool](https://github.com/hwbuehler/hgsfp-survey-tool);
die Zahlen sind gegen das dortige Original getestet (`tests/test_analysis.py`).

| Bereich | Sprache |
|---|---|
| Dashboard / Editor | Deutsch |
| Teilnehmer-Formular | Englisch |
| Reports | Englisch |

---

## Schnellstart

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env      # SECRET_KEY eintragen, siehe unten
alembic upgrade head
python scripts\bootstrap_template.py    # Vorlage aus reference/surveyjs-original.json
python scripts\fetch_model.py           # Clustering-Modell bereitstellen
python scripts\create_user.py --username admin

uvicorn app.main:app --reload
```

Dashboard: <http://127.0.0.1:8000/dashboard>

Einen Schlüssel für `.env` erzeugen:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Immer nur ein Worker.** SQLite hat genau einen Schreiber, und der Analyse-Cache liegt im Prozess.
Bei 300 Teilnehmer:innen ist das kein Engpass — gemessen 300 gleichzeitige Abgaben in unter
einer Sekunde (`scripts/load_test.py`).

---

## Ablauf einer Umfrage

1. **Anlegen** — im Dashboard Titel eingeben und Vorlage wählen. Die Zeitfenster kommen aus der
   Vorlage, je Zeitfenster **eine Beispiel-Vorlesung** als Vorlage zum Überschreiben — nicht die
   komplette Aufstellung des letzten Durchgangs, die man sonst erst löschen müsste.
   Alternativ direkt **aus einer früheren Ergebnisdatei**:
   Umfrage, Antworten und Vorlesungsliste entstehen in einem Schritt, die Umfrage wird als
   *geschlossen* angelegt (Archivdaten nehmen keine neuen Antworten an).
2. **Bearbeiten** (`/surveys/{id}/edit`) — Titel anpassen, Vorlesungen eintragen. Jede Vorlesung
   hat einen **Titel** (was Teilnehmende im Menü lesen) und einen **Schlüssel** (was in der
   Ergebnisdatei landet, z. B. der Nachname). „did not attend“ wird automatisch ergänzt.
   Titel und Schlüssel sind direkt in der Liste bearbeitbar — kein Löschen und Neuanlegen.
   Eingaben werden 1,5 s nach der letzten Änderung automatisch gespeichert; jede Speicherung
   bestätigt mit Uhrzeit, neu angelegte Zeilen werden kurz hervorgehoben.

   **Schlüssel ändern:** der Schlüssel steht auch in jeder bereits abgegebenen Antwort. Wird er
   geändert, stellt das Dashboard diese Antworten mit um und nennt die Anzahl in der Bestätigung —
   sonst erschiene die Vorlesung in der Auswertung unter einem Namen, den es im Editor nicht mehr
   gibt. Eine bereits gelaufene Analyse wird dadurch veraltet; die Auswertungsseite weist darauf
   hin und bietet „Analyse neu berechnen“ an.
3. **Prüfen** — veröffentlichen lässt sich erst, wenn jedes Zeitfenster mindestens eine Vorlesung
   hat und keine Schlüssel doppelt vorkommen; der Editor listet offene Punkte auf.
4. **Veröffentlichen** (`/surveys/{id}/distribute`) — freischalten, Link kopieren, oder die
   fertige **16:9-Einladungsfolie als PDF** herunterladen (Logo, Hinweistext, QR-Code, Link) und
   am Ende der Vorlesung projizieren. Der reine QR-Code bleibt zusätzlich als PNG verfügbar.
5. **Schließen**, sobald genug Antworten da sind.
6. **Auswerten** (`/surveys/{id}/report`) — „Analyse starten“, dann Vorschau je Vorlesung und
   Export als Einzel-PDF oder ZIP.

Die Startseite führt zu jeder Umfrage Erstellung, letzte Bearbeitung, Veröffentlichung und
Schließung auf (UTC), damit der zeitliche Ablauf ohne Nachschlagen erkennbar ist.

Weitere Ergebnisdateien lassen sich zu einer bestehenden Umfrage unter *Daten* nachimportieren.

### Kommentare im Report

Kommentare erscheinen an zwei Stellen: auf der jeweiligen Vorlesungsseite und gesammelt auf der
Seite **Lecture Comments** (Teil von „Overall“ und Gesamt-Report) — das Gegenstück zum
`comments_all_lectures.pdf` des alten Tools. Gab es keine Kommentare, steht das ausdrücklich da;
stillschweigend leere Abschnitte wären von einem Defekt nicht zu unterscheiden.

---

## Aufbau

```
app/
  main.py          FastAPI-App, Session-Middleware, Router
  config.py        Einstellungen aus .env
  db.py            SQLite-Engine mit WAL-PRAGMAs
  models.py        Datenmodell
  security.py      argon2-Hashing, Cookie-Session
  blueprint.py     SurveyJS-Original -> wiederverwendbare Blaupause
  slide.py         16:9-Einladungsfolie als PDF (fpdf2)
  surveyjs.py      Blaupause + Slots -> fertige SurveyJS-Definition; Antwort-Validierung
  routers/         auth · dashboard · editor · distribution · participation · reports
  analysis/        Portierung aus hgsfp-survey-tool, siehe unten
  templates/       Jinja2 (Dashboard deutsch, Formular und Report englisch)
  static/          CSS, htmx, self-gehostete SurveyJS-Runtime, Logo
reference/         Original-Fragebogen und Beispiel-Ergebnisse
scripts/           bootstrap_template · create_user · fetch_model · load_test
deploy/            Dockerfile, docker-compose.yml, Caddyfile
```

### Parametrisierung des Fragebogens

`reference/surveyjs-original.json` ist der handgeschriebene Fragebogen. Zwischen zwei Durchläufen
ändern sich daran nur der Titel und die Dropdown-Einträge — Fragetexte, Skalen und die
`visibleIf`-Logik nicht.

`scripts/bootstrap_template.py` zerlegt die Datei deshalb einmalig in eine Blaupause: eine
Vorlesungs-Seite mit `{prefix}` als Platzhalter, die Vorschlags-Seite und die Grundeinstellungen.
`app/surveyjs.py` klont die Seite je Zeitfenster und setzt Präfix und Auswahlliste ein. Dadurch
bleibt jedes Original-Attribut erhalten und die Zahl der Zeitfenster ist frei — drei ist der
heutige Fall, keine Grenze.

`tests/test_surveyjs.py` fährt den Weg rückwärts und vergleicht mit dem Original, damit beim
Generieren nichts verloren geht.

Beim Extrahieren wird bewusst vereinheitlicht:

| Stelle im Original | Änderung |
|---|---|
| `{"default": …, "de": …}` | auf den englischen Text reduziert |
| `noneText` | entfällt (uneinheitlich gesetzt, ohne `showNoneItem` wirkungslos) |
| `isRequired` fehlt bei `ml_level` | überall gesetzt |
| `displayMode` fehlt bei `ml_exciting`, `ml_structure` | überall `buttons` — sonst erscheinen dieselben Fragen auf der Vormittagsseite als Sterne, auf den anderen Seiten als Buttons |
| `completedHtml`: „survey!You“ | Leerzeichen ergänzt |
| `logo` von `hgsfp.uni-heidelberg.de` | lokale Kopie unter `/static/img/` — ein externer Request würde die IP jeder teilnehmenden Person an einen Fremdserver übertragen |

### Analyse

Portiert aus `survey_analyzer.py`, das Berechnung und fpdf-Ausgabe in einer Klasse mischte.
Übernommen wurde nur die Berechnung; die Darstellung ist jetzt HTML + CSS.

| Modul | Aufgabe |
|---|---|
| `constants.py` | Fragensatz und Likert-Skalen, unverändert übernommen |
| `aggregate.py` | Antworten nach Vorlesung gruppieren |
| `statistics.py` | Mittelwert, Standardabweichung, Verteilungen |
| `clustering.py` | Kommentare gruppieren (sentence-transformers, lazy geladen), siehe unten |
| `charts.py` | Likert-Balken als Inline-SVG |
| `report.py` | Report-Kontext je Abschnitt |
| `pipeline.py` | Hintergrund-Job, schreibt den Cache |
| `pdf.py` | HTML → PDF, ZIP-Bündel |
| `legacy_import.py` | alte Ergebnisdateien einlesen |

Zwei Regeln aus dem Original bleiben absichtlich erhalten: die Standardabweichung ist die
Stichproben-Standardabweichung (`ddof=1`), und bei fünf oder weniger Antworten wird keine
Statistik ausgewiesen.

#### Kommentar-Clustering

Das Original übernahm scikit-learns Voreinstellung `metric="euclidean"`, `linkage="ward"` mit
`distance_threshold=0.5`. Da das Modell L2-normierte Vektoren liefert, entspricht das einer
geforderten Kosinus-Ähnlichkeit von 0,875 — höher als das ähnlichste Paar in den Beispieldaten
(0,873). Es konnte nichts zusammengefasst werden, die „geclusterten" Kommentare waren stets mit
den rohen identisch.

Jetzt `metric="cosine"`, `linkage="average"`; die Schwelle ist damit direkt als Kosinus-Distanz
lesbar. Die beiden Freitextfelder verhalten sich unterschiedlich und bekommen eigene Werte
(`app/config.py`):

| Feld | Distanz | Wirkung |
|---|---|---|
| `sugg_organization` | `0.45` (cos ≥ 0,55) | Prosa zu wiederkehrenden Themen — fasst „mehr Kuchen", „bessere Kaffeepausen" sinnvoll zusammen |
| `sugg_topics` | `0.30` (cos ≥ 0,70) | kurze Stichworte — nur echte Dubletten („2D materials"/„2d Materials"); lockerer gruppiert das Modell „Mathematical Physics" mit „Physical Chemistry" und verfälscht damit die Nennungszahlen |

Die Überschrift einer Gruppe ist ihr Medoid, nicht das erste Element — sonst hinge sie davon ab,
in welcher Reihenfolge die Antworten eingegangen sind.

Charts entstehen ohne matplotlib. Der Balken ist eine einzelne gestapelte Reihe; direkt
erzeugtes SVG erlaubt die 2px-Lücken zwischen den Segmenten und die abgerundeten Außenkanten,
und der Prozenttext bleibt in Vorschau **und** PDF echter Text statt Buchstabenpfade.

Der Analyse-Cache speichert Zahlen, keine Grafiken — die SVGs werden beim Rendern aus den
gecachten Statistiken erzeugt. Eine Änderung am Chart- oder Report-Design wirkt deshalb sofort,
ohne dass die Analyse (und damit das Clustering) neu laufen muss.

### Gemeinsames Design für Vorschau und PDF

`app/templates/report/document.html` und `app/static/css/report.css` werden von beiden Wegen
genutzt: die Vorschau rendert sie im Browser, der Export schickt dieselbe Ausgabe durch
WeasyPrint. Die `@page`-Regeln stehen in derselben Datei wie die Bildschirmregeln — ein
Auseinanderlaufen ist damit ausgeschlossen.

---

### Einladungsfolie

`app/slide.py` erzeugt die Folie mit **fpdf2**, nicht mit WeasyPrint. Die Folie soll kurz vor
einer Vorlesung auf einem beliebigen Laptop entstehen können; WeasyPrint braucht dafür Pango und
cairo, die unter Windows nicht per `pip` kommen (siehe unten). fpdf2 ist reines Python und
zeichnet ein festes Layout wie dieses ohne Hilfe. Das Logo wird als SVG eingebettet, bleibt also
vektoriell.

Die Kern-PDF-Schriften können nur Latin-1. Umlaute sind abgedeckt, typografische Zeichen wie „—"
nicht — `_latin1()` ersetzt sie, damit ein Gedankenstrich im Titel den Download nicht abbricht.

---

## PDF-Export unter Windows

WeasyPrint braucht Pango, cairo und gdk-pixbuf. Diese Bibliotheken lassen sich nicht per `pip`
installieren, weshalb der Export auf einem frischen Windows-Rechner mit einer Meldung
fehlschlägt statt zu funktionieren. Analyse und Browser-Vorschau laufen davon unberührt weiter.

Drei Wege:

1. **Im Browser drucken** — kein Setup nötig. Die Vorschau hat oben rechts
   „Drucken / Als PDF speichern“; das Ergebnis nutzt dieselben `@page`-Regeln wie der
   Server-Export.
2. **GTK-Runtime installieren** — für den serverseitigen Export und den ZIP-Download lokal:
   [GTK-for-Windows-Runtime-Environment-Installer](https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases),
   danach die Shell neu öffnen.
3. **Im Container** — das Image unter `deploy/` bringt die Bibliotheken mit, sodass in der
   Produktion nichts weiter zu tun ist.

Ob der Export auf einer Maschine funktioniert, prüft `scripts/verify_pdf.py`: es rendert alle
Report-Abschnitte aus `reference/sample-survey-results.json` einzeln und als ZIP, ohne Server
und ohne Datenbank.

```bash
python scripts/verify_pdf.py                    # lokal
docker run --rm -v "$PWD/build:/app/build" hgsfp-dashboard:test \
    python scripts/verify_pdf.py --skip-clustering
```

---

## Datenschutz

Die Umsetzung folgt der Vorgabe, dass Antworten anonym sind:

- **Keine identifizierenden Daten.** Weder Name noch E-Mail, IP-Adresse, User-Agent oder
  Session-Cookie werden zu einer Antwort gespeichert.
- **Kein Zeitstempel, nur ein Datum.** Die Uhrzeit einer Abgabe würde eine Zuordnung erlauben.
- **Zufällige IDs.** Antworten bekommen eine UUID statt einer laufenden Nummer, damit sich die
  Reihenfolge der Abgaben nicht rekonstruieren lässt.
- **Keine Drittanbieter.** SurveyJS-Runtime, htmx, Schriften und Logo sind lokal abgelegt; das
  Formular lädt nichts von fremden Servern.
- **Hinweis vor dem Start.** Über dem Fragebogen steht ein kurzer Datenschutzhinweis, ausführlich
  unter `/privacy` — dort sind Verantwortliche und Datenschutzbeauftragte noch einzutragen.
- **Löschung.** Unter *Daten* lassen sich einzeln die Antworten oder die gesamte Umfrage samt
  Auswertungen entfernen.
- **Passwörter** werden mit argon2 gehasht.
- **TLS** übernimmt der Reverse Proxy; die `Caddyfile` setzt HSTS und eine restriktive CSP und
  filtert IP-Adressen aus dem Zugriffsprotokoll.

Der Import alter Ergebnisdateien verwirft `HappendAt` und `InstanceId`.

---

## Tests

```powershell
pytest
```

- `tests/test_analysis.py` — vergleicht Mittelwerte, Standardabweichungen, n, DnA-Zählungen und
  Kommentarzahlen mit `tests/data/golden_statistics.json`, das aus dem Original-Analyzer stammt.
  Schlägt hier etwas fehl, hat die Portierung eine Zahl verändert.
- `tests/test_surveyjs.py` — Round-Trip des Fragebogens, Antwort-Validierung, Charts.
- `tests/test_api.py` — Auth-Grenze, öffentliches Formular, Import/Export, Löschung.

Lasttest gegen einen laufenden Server:

```powershell
python scripts\load_test.py http://127.0.0.1:8000/s/<token> --count 300
```

---

## Deployment

```bash
cp deploy/.env.example deploy/.env    # SECRET_KEY, SITE_ADDRESS, BASE_URL eintragen
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

docker compose -f deploy/docker-compose.yml exec app python scripts/fetch_model.py
docker compose -f deploy/docker-compose.yml exec app python scripts/bootstrap_template.py
docker compose -f deploy/docker-compose.yml exec -it app python scripts/create_user.py --username admin
```

Caddy holt für einen öffentlichen Hostnamen automatisch ein Zertifikat. Auf einem rein internen
Server stattdessen den `tls`-Eintrag in der `Caddyfile` auf das Zertifikat der Uni-CA zeigen
lassen.

Der Umzug in ein anderes Rechenzentrum ist ein Kopiervorgang: Compose-Datei plus die Volumes
`survey-data` (Datenbank) und `survey-models` (Clustering-Modell).

### Sicherung

Die Datenbank liegt im Volume `survey-data`. Eine konsistente Kopie im laufenden Betrieb:

```bash
docker compose -f deploy/docker-compose.yml exec app \
  python -c "import sqlite3,shutil; sqlite3.connect('/app/data/hgsfp.db').backup(sqlite3.connect('/app/data/backup.db'))"
```

Wegen WAL reicht es **nicht**, `hgsfp.db` allein zu kopieren — entweder die drei Dateien
(`.db`, `.db-wal`, `.db-shm`) zusammen sichern oder den Weg oben nehmen.
