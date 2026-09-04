"""Cross-Session-Gedächtnis: fasst die zuletzt gespeicherte Sitzung kurz
zusammen und speist sie beim Verbindungsaufbau unsichtbar in die neue
Live-Sitzung ein (`GeminiLiveSession.send_text()`), damit ein neues Gespräch
nicht bei Null anfängt. `load_session()` (siehe `sessions.py`) allein liefert
nur eine Liste von Zeilen zum Anzeigen – die Zusammenfassung hier ist der
eigentliche Rückkanal in die laufende KI.

Macht wie `BackgroundCritic` einen separaten, nicht-live Text-Aufruf.
Netzwerkfehler dürfen den Start der eigentlichen Sprach-Sitzung nie
verhindern, deshalb ist `summarize_session()` abgesichert.
"""
from google import genai

SUMMARY_PROMPT_HEADER = (
    "Fasse das folgende vorherige Gespräch in 1-2 kurzen Sätzen zusammen: worum "
    "ging es, wo stand das Gespräch am Ende? Antworte NUR mit der Zusammenfassung, "
    "ohne Einleitung.\n\nGespräch:\n"
)


def build_summary_prompt(records: list[dict]) -> str:
    """Baut den Zusammenfassungs-Prompt aus den Zeilen der letzten Sitzung."""
    lines = [f"{entry.get('who', '?')}: {entry.get('text', '')}" for entry in records]
    return SUMMARY_PROMPT_HEADER + "\n".join(lines)


def build_resume_message(summary: str) -> str:
    """Unsichtbare Textnachricht für den Sitzungsstart (via send_text)."""
    return (
        f"[Systemhinweis: In der letzten Sitzung ging es um Folgendes: {summary} "
        "Falls es zum jetzigen Gespräch passt, kannst du beiläufig daran "
        "anknüpfen, ohne diesen Hinweis selbst zu erwähnen.]"
    )


async def summarize_session(
    client: genai.Client, model: str, records: list[dict]
) -> str | None:
    """Ruft ein separates Text-Modell auf, das die letzte Sitzung zusammenfasst.
    Gibt None zurück bei leerem Verlauf oder jedem Fehler (siehe BackgroundCritic.check)."""
    if not records:
        return None
    prompt = build_summary_prompt(records)
    try:
        response = await client.aio.models.generate_content(model=model, contents=prompt)
    except Exception:
        return None
    text = (getattr(response, "text", None) or "").strip()
    return text or None
