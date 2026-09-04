# Sprach-Assistent – Phase 4

Sprachgesteuerter Entwicklungs-Assistent: Du sprichst ins Mikro, die KI (Gemini) antwortet per Stimme. Ein Fenster zeigt Status und Gesprächsverlauf, ein Knopf schaltet das Mikro stumm, Sitzungen lassen sich speichern/wieder öffnen, die Stimme und der Duo-Mode lassen sich in den Einstellungen ändern, und ein stiller Hintergrund-Prüfer kann die KI auf Logikfehler in deiner Idee hinweisen.

## Was du einmalig brauchst

1. **Python 3.10 oder neuer** installieren.
   - Windows: von python.org laden. **Wichtig:** Im Installer den Haken bei „Add python.exe to PATH" setzen.
   - Linux: `sudo apt install python3 python3-venv portaudio19-dev` (das portaudio-Paket braucht es für den Sound).
2. **Gemini-API-Key** besorgen (kostenlos auf Google AI Studio: https://aistudio.google.com/apikey).

## Einrichtung (einmalig)

Im Projektordner ein Terminal öffnen:

**Windows:**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Linux:**
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Dann die Datei `config.example.json` kopieren und als `config.json` speichern. Deinen API-Key dort bei `"api_key"` eintragen.

## Starten

```
python main.py
```

Ein Fenster geht auf. Sobald der Punkt grün wird („Verbunden"), kannst du sprechen. Die KI hört zu und antwortet per Stimme; der Text des Gesprächs läuft unten mit.

## Tests ausführen

```
pytest
```

Testet die Logik-Module (`config.py`, `sessions.py`, `duo_mode.py`, `background_critic.py`) ohne echtes Mikro/Lautsprecher/API — die Gemini-Aufrufe im Hintergrund-Prüfer werden dabei durch einen Fake-Client ersetzt. `main.py` selbst (die Flet-Oberfläche) hat keine automatisierten Tests; das wird per Hand geprüft (`python main.py` starten und ausprobieren).

## Die Dateien im Überblick

| Datei | Zweck |
|---|---|
| `main.py` | Startpunkt: Fenster (Flet) + verbindet alles |
| `audio_engine.py` | Mikro rein, Lautsprecher raus |
| `gemini_session.py` | Verbindung zur KI, sendet Hördaten, empfängt Antworten |
| `config.py` | Liest/schreibt config.json |
| `config.json` | Deine Einstellungen (API-Key, Stimme, Modell, Duo-Mode, Hintergrund-Prüfer) |
| `sessions.py` | Gesprächsverlauf speichern/laden (Ordner `sessions/`) |
| `duo_mode.py` | Phase 3: Regeln für die zwei Denkrollen |
| `background_critic.py` | Phase 4: der stille Kritiker (Closed-Loop) |
| `tests/` | Pytest-Tests für die Logik-Module |

## Einstellungen in config.json

- `api_key`: dein Gemini-Key (Pflicht)
- `voice`: Stimme der KI (z. B. `Aoede`, `Charon`, `Kore`, `Puck`, `Fenrir`) — auch über das Zahnrad-Symbol im Fenster änderbar
- `model`: welches Gemini-Modell (voreingestellt passt)
- `system_instruction`: die „Persönlichkeit" – hier stellst du später den Senior-Developer-Mode ein
- `duo_mode`: `"off"` | `"auto"` (Rollenwechsel nach jeder Antwort) | `"manual"` (Rollenwechsel per Knopf im Fenster) — auch über das Zahnrad-Symbol änderbar
- `critic_enabled`: `true`/`false` — schaltet den Hintergrund-Prüfer (Phase 4) an/aus
- `critic_model`: welches (nicht-live) Gemini-Textmodell der Prüfer benutzt
- `critic_check_every`: nach wie vielen eigenen Gesprächsbeiträgen der Prüfer nachschaut

## Troubleshooting (Kurzfassung)

- **„Kein API-Key gefunden"** → config.json fehlt oder Key nicht eingetragen.
- **Fehler beim Start unter Linux wegen Audio** → `sudo apt install portaudio19-dev` nachinstallieren.
- **Kein Ton / kein Mikro** → prüfen, ob Windows/Linux das richtige Standard-Gerät nutzt; das Programm nimmt jeweils das Standardgerät.
- **Fenster bleibt leer / Fehler „403"** → meist ungültiger oder abgelaufener API-Key.

## Das Projekt in einem Satz

Eine App, mit der du per Sprache mit einer KI über deine Geschäftsideen sprichst — sie antwortet sofort per Stimme, spielt dabei zwei Denkrollen und meldet sich von selbst, wenn sie einen Fehler in deiner Logik findet.

## Die 4 Grundsatzentscheidungen

1. **Duo-Mode:** Wir bauen *eine* KI, die beide Rollen selbst wechselt, statt zwei getrennten Persönlichkeiten hin- und herzuschalten. Grund: die Verbindung zur KI legt die Persönlichkeit am Anfang fest (`system_instruction` in `gemini_session.py`) und kann sie nicht mitten im Gespräch neu setzen. Eine Instruktion, die die KI zum selbstständigen Rollenwechsel anweist, bringt das gleiche Ergebnis — nur ohne Pause/Reconnect.
2. **Stimmen:** Wir nehmen eine der fertigen Gemini-Stimmen (ca. 30 zur Auswahl). Eine komplett maßgeschneiderte Stimme geht nicht, ohne Verzögerung ins Gespräch zu bringen — das Gegenteil von dem, was gewollt ist.
3. **Closed-Loop (die Hintergrund-Kritik):** Bleibt drin, ist das stärkste Stück am Konzept — kommt aber erst in Phase 4, weil es auf allem anderen aufbaut.
4. **Audio:** Der schwierigste Teil beim Testen, kein Konzeptfehler. Wird als Erstes gebaut, damit die meiste Zeit für genau diesen Teil bleibt.

## Der Plan in 4 Phasen

### Phase 1 — Sprechen (das Fundament) ✅ umgesetzt

- Du sprichst ins Mikrofon, die KI antwortet per Stimme, fast ohne Wartezeit.
- Ein einfaches Fenster mit einem einzigen Knopf: Stumm an/aus.
- **Fertig, wenn:** 10 Minuten am Stück sprechen, ohne dass Ton abbricht oder die App abstürzt.

### Phase 2 — Komfort ✅ umgesetzt

- Textfenster daneben: schriftlich mitlesen, was die KI sagt (praktisch zum Nachlesen und Kopieren).
- Sitzungen speichern und wieder öffnen (Knöpfe unten im Fenster; Dateien liegen als JSON in `sessions/`).
- Einstellung für gewählte Stimme (Zahnrad-Symbol oben rechts; wirkt nach Neustart der App).
- **Sprechtempo entfällt:** Die Gemini-Live-API bietet keinen Parameter dafür (nur Stimmen-Auswahl + Sprache), ein Nachbau per Audio-Resampling würde die Stimme verzerren (Pitch-Shift). Bewusst gestrichen statt notdürftig nachgebaut.

### Phase 3 — Die zwei Denkrollen ✅ umgesetzt

- Eine Anweisung an die KI (`duo_mode.py`): Sie antwortet abwechselnd als **Visionär** (denkt groß, sieht Chancen) und als **Pragmatiker** (prüft auf Realismus, Geld, Aufwand).
- Einstellbar, wie oft gewechselt wird — `duo_mode: "auto"` (nach jeder Antwort, die KI wechselt selbstständig) oder `duo_mode: "manual"` (Rollenwechsel nur per „Rolle wechseln"-Knopf im Fenster, über eine unsichtbare Textnachricht via `send_text()`).
- Kein Verbindungswechsel nötig (siehe Entscheidung 1) — beide Modi laufen über eine einzige, angereicherte `system_instruction`.

### Phase 4 — Der stille Kritiker (Closed-Loop) ✅ umgesetzt

- Ein zweiter, unsichtbarer Prozess (`background_critic.py`) liest in Abständen (`critic_check_every` eigene Gesprächsbeiträge) den Verlauf mit — über einen separaten, nicht-live Text-Aufruf, nicht über die Sprachverbindung.
- Findet er einen Fehler oder Widerspruch in der Idee, flüstert er der KI unauffällig etwas ins Ohr (über `send_text()` in `gemini_session.py`) — und die KI bringt es natürlich ins Gespräch ein.
- Nutzer merkt nichts davon, außer dass die KI plötzlich klügere Einwände macht.
- Aus mit `critic_enabled: false` (Standard) — schaltbar in `config.json`.

## Was wir bewusst weglassen

- Eigene künstliche Stimmen (zu langsam)
- Wechsel zwischen zwei getrennten KI-Persönlichkeiten (technisch nicht sauber möglich)
- Alles, was nicht dem Kern dient: keine Anmeldung, keine Cloud, kein Design-Feinschliff
