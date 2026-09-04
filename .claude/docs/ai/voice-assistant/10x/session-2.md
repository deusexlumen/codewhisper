# 10x Analysis: CodeWhisper (Sprachgesteuerter Entwicklungs-Assistent)
Session 2 | Date: 2026-09-04

## Current Value
Seit Session 1 gebaut: `code_context.py` (Clipboard → unsichtbar in Live-Sitzung, manueller Knopf), `background_critic.py` prüft jetzt zusätzlich gegen den Zwischenablagen-Code (nicht nur Transkript-Logik), `session_memory.py` speist beim Connect automatisch eine Zusammenfassung der letzten Sitzung ein. Repo ist jetzt ein echtes Git-Repo (`github.com/deusexlumen/codewhisper`), 58 Tests grün.

**Wo der Wert jetzt steht**: Die KI kennt inzwischen echten Code (auf Klick) und den letzten Gesprächsstand (automatisch). Sie kann darüber *reden* — aber nichts *tun*. Und: alles, was automatisch im Hintergrund passiert (Zusammenfassung beim Connect, Critic-Check gegen Clipboard), passiert komplett unsichtbar — der Nutzer sieht im Transkript-Fenster nie, dass es passiert ist.

**Noch offen aus Session 1, nie gebaut**: die zwei "Do Now"-Punkte (Auto-Save bei Disconnect, Critic-Incoming-UI-Cue) und der globale Hotkey wurden übersprungen zugunsten der "Do Next"-Punkte — bleiben liegen, sind aber immer noch billig.

## The Question
Nach Code-Grounding + Kritiker-Grounding + Session-Memory: was ist der nächste Hebel, der am meisten Wert bringt?

---

## Massive Opportunities

### 1. Function-Calling — die KI kann Dinge tun, nicht nur reden
**What**: `google-genai`-Tool-Deklarationen für ein festes Allowlist-Set: `pytest ausführen`, `git status`, `git diff`, `git log -n 5`. Modell fordert Aufruf an, `gemini_session.py` führt lokal aus (nur die Allowlist, nie freier Shell-Zugriff), Ergebnis geht als Turn zurück.
**Why 10x**: Das war in Session 1 "Explore" wegen fehlendem Permission-Modell — die Grundlage dafür ist jetzt da: echtes Git-Repo, TDD-Testsuite, Text-Client-Pattern (`background_critic`/`session_memory`) für separate Aufrufe schon etabliert. Der natürliche nächste Schritt der Werttreppe: reden → echten Code sehen → gegen echten Code prüfen (alles Session 1+2 bisher) → jetzt **verifizieren, indem man's tatsächlich ausführt**. "Sag mir, ob der Test jetzt grün ist" wird ein gesprochener Satz mit echter Antwort, ohne Tastatur.
**Unlocks**: Voice-TDD — Test schreiben lassen, laut "führ die Tests aus" sagen, Ergebnis hören.
**Effort**: Hoch — Allowlist + Ausführungs-Sandbox im Projektverzeichnis, keine freie Shell.
**Risiko**: Sicherheit. Hart auf read-only/sichere Befehle beschränken (`pytest`, `git status/diff/log`), niemals beliebige Kommandos.
**Score**: 🔥 Must do — größter verbleibender Hebel im Backlog.

### 2. Live-Datei-Beobachtung statt Zwischenablage
**What**: Statt "Code-Kontext senden"-Knopf + manuellem Kopieren: ein Datei-Pfad wird einmal ausgewählt (z.B. die gerade bearbeitete Datei), `watchdog`/Polling erkennt Änderungen, injiziert automatisch den Diff seit letztem Stand.
**Why 10x**: Entfernt die letzte manuelle Reibung im Kern-Feature aus Session 1 — kein Copy-Paste mehr nötig, der Kontext ist immer aktuell statt "so aktuell wie der letzte Klick".
**Effort**: Hoch (Dateisystem-Watcher, Diff-Berechnung, Rate-Limiting gegen zu häufige Injektion).
**Risiko**: Zu viele/zu kleine Injektionen bei jedem Tastendruck — braucht Debounce.
**Score**: 🤔 Maybe — echter Mehrwert, aber Function-Calling (#1) bringt mehr pro Aufwand.

---

## Medium Opportunities

### 1. Sichtbarkeit für die unsichtbaren Injektionen
**What**: Wenn `session_memory` beim Connect eine Zusammenfassung einspeist oder der Critic einen Hinweis flüstert, erscheint eine leise, klar markierte Zeile im Transkript-Fenster (nur lokal, nicht Teil der KI-Konversation) — z.B. "🧵 Letzte Sitzung eingespielt: …" oder "🕵️ Kritiker-Hinweis gesendet".
**Why 10x**: Beide gerade gebauten Features (Session-Memory, Critic+Code-Kontext) sind komplett unsichtbar — der Nutzer hat keine Ahnung, *dass* sie wirken, nur dass die KI plötzlich "schlauer" antwortet. Das ist zwar Konzept-treu ("die Person merkt nichts"), untergräbt aber Vertrauen: kein Beweis, dass die neuen Features überhaupt laufen.
**Impact**: Macht den bereits gebauten Wert erst sichtbar/nachvollziehbar — reine UI-Änderung, kein neuer Gemini-Call.
**Effort**: Niedrig (ein `add_transcript_line`-artiger Aufruf an den bestehenden Injektionsstellen in `main.py`).
**Score**: 🔥 Must do

### 2. Kritiker merkt sich offene Punkte über Sitzungen hinweg
**What**: `session_memory`s Zusammenfassung fließt aktuell nur in die Haupt-KI. Den gleichen Kanal nutzen, um dem *Kritiker* zu sagen, was er letztes Mal bemängelt hat — prüft er in dieser Sitzung, ob der Punkt behoben wurde.
**Why 10x**: Verbindet die zwei gerade gebauten Features zu etwas, das über die Summe hinausgeht — ein Kritiker mit Gedächtnis statt nur pro Sitzung.
**Effort**: Niedrig-Mittel (Wiederverwendung von `session_memory.summarize_session` mit anderem Prompt-Fokus, oder Weitergabe des letzten Hinweises an `background_critic.build_critic_prompt`).
**Score**: 👍 Strong

---

## Small Gems

### 1. Auto-Save bei Disconnect *(aus Session 1, immer noch offen)*
**What**: `shutdown()` in `main.py` ruft `save_session(transcript_log)`, falls nicht leer.
**Why powerful**: Verhindert Datenverlust beim Fenster-Schließen ohne Klick auf „Sitzung speichern" — und macht `session_memory` (Bonus aus dieser Session) überhaupt zuverlässig nutzbar, weil sonst oft gar keine Sitzung zum Zusammenfassen vorliegt.
**Effort**: Trivial.
**Score**: 🔥 Must do — jetzt sogar wichtiger als in Session 1, weil Cross-Session-Memory drauf aufbaut.

### 2. Globaler Mikro-Hotkey *(aus Session 1, immer noch offen)*
**What**: Systemweiter Hotkey zum Stummschalten, unabhängig vom Fensterfokus.
**Effort**: Mittel (`keyboard`-Paket + Flet-Anbindung).
**Score**: 👍 Strong

### 3. „Kontext gesendet"-Badge am Knopf selbst
**What**: `send_context_button` zeigt kurz einen Haken/Text-Wechsel nach Klick ("✓ Gesendet"), statt nur eine Status-Zeile ganz oben.
**Why powerful**: Session 1 hat den Button gebaut, aber Feedback läuft nur über die weit entfernte Status-Zeile — leicht zu übersehen.
**Effort**: Trivial.
**Score**: 🤔 Maybe

---

## Recommended Priority

### Do Now (Quick wins)
1. **Auto-Save bei Disconnect** — ein Zweizeiler, macht Session-Memory zuverlässig.
2. **Sichtbarkeit der unsichtbaren Injektionen** — macht die letzten beiden Sitzungen sichtbar wirksam statt gefühlt wirkungslos.

### Do Next (High leverage)
1. **Function-Calling (Allowlist: pytest/git)** — der eigentliche nächste Sprung: von "redet über Code" zu "verifiziert Code".
2. **Kritiker-Gedächtnis über Sitzungen** — billige Erweiterung von bereits vorhandenem Code.

### Explore (Strategic bets)
1. **Live-Datei-Beobachtung statt Zwischenablage** — entfernt letzte Reibung, aber Function-Calling zuerst.
2. Vision/Screen-Share (aus Session 1, weiterhin ungebaut, weiterhin größter Lift).

### Backlog (Good but not now)
1. Globaler Mikro-Hotkey.
2. „Kontext gesendet"-Badge am Knopf.

---

## Questions

### Answered
- **Q**: Wurden die Session-1-„Do Now"-Punkte gebaut? **A**: Nein — Code bestätigt (`grep` über `main.py`): kein Auto-Save-Aufruf in `shutdown()`, keine Critic-Incoming-UI. Beide noch offen.

### Blockers
- **Q**: Für Function-Calling — reicht ein hartes Allowlist (`pytest`, `git status/diff/log`) oder soll später auch Datei-Lesen (nicht Schreiben) erlaubt sein? Beeinflusst die Tool-Deklaration im ersten Wurf.
- **Q**: Live-Datei-Beobachtung — auf eine Datei fest verdrahten oder Auswahl-Dialog? Auswahl-Dialog ist mehr UI-Aufwand, fest verdrahtet ist unflexibel für Multi-Datei-Debugging.

## Next Steps
- [ ] Die zwei „Do Now"-Punkte bauen (klein, schnell, Vertrauen schaffen).
- [ ] Function-Calling-Design festlegen: Allowlist final, Ausführungsort (Projektverzeichnis via `subprocess`), Fehlerbehandlung analog `BackgroundCritic.check()`.
- [ ] Entscheiden: Kritiker-Gedächtnis vor oder nach Function-Calling.
