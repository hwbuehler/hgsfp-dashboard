# HGSFP Umfrage-Dashboard

Analysis dashboard for SurveyJS feedback exports from the Heidelberg Physics Graduate Days.
Import a JSON result file, review the analysis in the browser, and export reports as PDFs.

Die Rechenkerne stammen aus [hgsfp-survey-tool](https://github.com/hwbuehler/hgsfp-survey-tool);
die Zahlen sind gegen das dortige Original getestet (`tests/test_analysis.py`).

| Bereich | Sprache |
|---|---|
| Dashboard | Englisch |
| Reports | Englisch |

---

## Run locally on macOS

The steps below use `uv` to install Python 3.13 into uv's managed tool directory and to create
the project environment. Homebrew supplies `uv` and the native Pango and libffi libraries needed
by WeasyPrint; Python packages stay inside `.venv`.

1. Install [Homebrew](https://brew.sh/) if it is not already installed, then install `uv` and
   WeasyPrint's native macOS libraries:

   ```bash
   brew install uv pango libffi
   ```

2. From the project directory, install Python 3.13 with `uv`, create the local virtual
   environment, and sync the Python packages listed in `requirements.txt`:

   ```bash
   uv python install 3.13
   uv venv --python 3.13
   source .venv/bin/activate
   uv pip sync requirements.txt
   ```

   The Python interpreter and installed packages are managed separately from macOS's system
   Python. Re-run `uv pip sync requirements.txt` after changing dependencies.

3. Create the local environment file and set a session-signing key:

   ```bash
   cp .env.example .env
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

   Copy the generated value into `HGSFP_SECRET_KEY` in `.env`. Keep `HGSFP_BASE_URL` as
   `http://127.0.0.1:8000` for local use.

4. Create or update the database schema, download the comment-clustering model, and create a
   dashboard login:

   ```bash
   alembic upgrade head
   python scripts/fetch_model.py
   python scripts/create_user.py --username admin
   ```

   The model download is several hundred megabytes and requires an internet connection. The
   script saves it under `models/all-MiniLM-L6-v2`; repeat the command after deleting that folder
   if you need to download it again.

5. Start the dashboard:

   ```bash
   export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
   uvicorn app.main:app --reload
   ```

   Open <http://127.0.0.1:8000/dashboard> and sign in with the account created above. Upload a
   SurveyJS results JSON file to begin.

**Run one worker only.** SQLite has one writer and the analysis job cache is in-process. For
local development, `--reload` is fine; do not add multiple Uvicorn workers.

### macOS PDF troubleshooting

The startup command above exposes Homebrew's library directory to the app process. If PDF export
reports that a native library cannot be loaded, run it again from the same Terminal session after
setting that path:

```bash
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
uvicorn app.main:app --reload
```

Homebrew's default prefix is `/opt/homebrew` on Apple Silicon and `/usr/local` on Intel Macs.
The Homebrew prefix lookup keeps the command correct for either architecture.

---

## Analysis workflow

1. Open the dashboard and upload a SurveyJS JSON result export. The analysis name is optional;
   when omitted, the uploaded filename is used.
2. The importer discovers fields ending in `_title`, including new course prefixes such as
   `sc_`, and stores each discovered result set for analysis.
3. Add a lecturer name and lecture title for each imported result key. Skill-course exports may
   identify one lecturer with multiple values (for example, `Pauline Gagnon 1` and
   `Pauline Gagnon 2`); give both rows the same lecturer name and enter each course title.
4. Start analysis to calculate statistics and group comments. Open report previews or download
   the full report. Skill-course PDF exports combine all course sections for each lecturer into
   one PDF; the reports ZIP follows the same grouping.

For skill courses, `sc_title` is the imported result key. The dashboard lets you assign a
display lecturer name and course title to each distinct key. Each course retains its own
statistics, and courses assigned to the same lecturer are bundled into one PDF.

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
  routers/         auth · dashboard · reports
  analysis/        Portierung aus hgsfp-survey-tool, siehe unten
  templates/       Jinja2 (Dashboard deutsch, Formular und Report englisch)
  static/          CSS, htmx, Report-Assets
reference/         Original-Fragebogen und Beispiel-Ergebnisse
scripts/           create_user · fetch_model · verify_pdf
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

## PDF export

Server-side PDF export uses the WeasyPrint Python package from `.venv` and Homebrew's Pango and
libffi native libraries. Browser previews also offer print-to-PDF and use the same report styles.

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
- **Obergrenzen für Eingaben.** Die öffentliche Abgabe-Route nimmt JSON von jeder Person mit dem
  Link entgegen, deshalb ist jede Eingabe begrenzt: Request-Body (`HGSFP_MAX_SUBMISSION_BYTES`,
  256 kB), Länge eines Freitextfeldes (`HGSFP_MAX_FREE_TEXT_CHARS`, 5000 Zeichen, wird gekürzt
  statt abgelehnt) und Upload-Größe beim Import (`HGSFP_MAX_UPLOAD_BYTES`, 25 MB). Ohne diese
  Grenzen kann eine einzige Anfrage die Datenbank beliebig groß machen — und das Clustering
  versucht anschließend, den Text einzubetten.
- **TLS** übernimmt der Reverse Proxy; die `Caddyfile` setzt HSTS und eine restriktive CSP und
  filtert IP-Adressen aus dem Zugriffsprotokoll.

Der Import alter Ergebnisdateien verwirft `HappendAt` und `InstanceId`.

---

## Tests

```bash
pytest
```

- `tests/test_analysis.py` — vergleicht Mittelwerte, Standardabweichungen, n, DnA-Zählungen und
  Kommentarzahlen mit `tests/data/golden_statistics.json`, das aus dem Original-Analyzer stammt.
  Schlägt hier etwas fehl, hat die Portierung eine Zahl verändert.
- `tests/test_surveyjs.py` — Round-Trip des Fragebogens, Antwort-Validierung, Charts.
- `tests/test_api.py` — Auth-Grenze, öffentliches Formular, Import/Export, Löschung.

Lasttest gegen einen laufenden Server:

```bash
python scripts/load_test.py http://127.0.0.1:8000/s/<token> --count 300
```

---

## Deployment

```bash
cp deploy/.env.example deploy/.env    # SECRET_KEY, SITE_ADDRESS, BASE_URL eintragen
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

docker compose -f deploy/docker-compose.yml exec app python scripts/fetch_model.py
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
