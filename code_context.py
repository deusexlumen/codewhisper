"""Code-Kontext-Grounding: liest die Zwischenablage und baut daraus eine
unsichtbare Textnachricht (über `GeminiLiveSession.send_text()`), damit die KI
reale Code-Ausschnitte kennt statt nur die gesprochene Beschreibung davon.

`read_clipboard()` kapselt den einzigen I/O-Aufruf hinter einer injizierbaren
`paste_fn`, damit die Logik ohne echte Zwischenablage testbar bleibt – Fehler
(z.B. keine Zwischenablage verfügbar) werden abgefangen und liefern None,
analog zu `BackgroundCritic.check()`.
"""
from typing import Callable, Optional

MAX_CONTEXT_CHARS = 4000


def build_context_message(clipboard_text: Optional[str], max_chars: int = MAX_CONTEXT_CHARS) -> Optional[str]:
    """Baut die unsichtbare Kontext-Nachricht, oder None wenn nichts Sinnvolles da ist."""
    if not clipboard_text or not clipboard_text.strip():
        return None
    text = clipboard_text.strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    note = " (gekürzt)" if truncated else ""
    return (
        f"[Systemhinweis: Der Nutzer hat folgenden Code-Ausschnitt aus der Zwischenablage "
        f"geteilt{note}. Beziehe dich darauf, wenn es zum Gespräch passt, ohne das Kopieren "
        "selbst zu erwähnen:\n\n"
        f"{text}]"
    )


def read_clipboard(paste_fn: Optional[Callable[[], str]] = None) -> Optional[str]:
    """Liest die Zwischenablage. Gibt None bei leerem Inhalt oder jedem Fehler zurück."""
    if paste_fn is None:
        import pyperclip

        paste_fn = pyperclip.paste
    try:
        text = paste_fn()
    except Exception:
        return None
    if not text or not text.strip():
        return None
    return text
