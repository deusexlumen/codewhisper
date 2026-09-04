"""Phase 4 – Der stille Kritiker (Closed-Loop).

Ein zweiter, unsichtbarer Text-Aufruf (nicht die Live-Verbindung) liest in
Abständen den bisherigen Gesprächsverlauf und sucht nach Widersprüchen oder
Logikfehlern in der besprochenen Geschäftsidee. Findet er etwas, wird der
Hinweis über `GeminiLiveSession.send_text()` unauffällig in die laufende
Live-Sitzung eingespeist – die Person merkt nur, dass die KI plötzlich einen
klugen Einwand bringt.

Netzwerkfehler beim Kritiker dürfen die laufende Sprach-Sitzung nie zum
Absturz bringen, deshalb ist der eigentliche Modell-Aufruf in `check()`
abgesichert.
"""
from google import genai

CRITIC_PROMPT_HEADER = (
    "Du bist ein stiller Hintergrund-Prüfer für ein Brainstorming-Gespräch über "
    "eine Geschäftsidee. Du liest NUR mit, du sprichst nicht direkt mit.\n\n"
    "Prüfe den folgenden Gesprächsausschnitt auf klare Logikfehler, Widersprüche "
    "oder unrealistische Annahmen (z.B. widersprüchliche Zahlen, ein Zielmarkt, "
    "der sich selbst ausschließt, eine Kostenannahme, die offensichtlich nicht "
    "aufgeht).\n\n"
    "Antworte NUR in einem der beiden Formate, ohne weiteren Text:\n"
    "OK\n"
    "HINWEIS: <ein kurzer, konkreter Satz, was zu prüfen wäre>\n\n"
    "Gesprächsausschnitt:\n"
)


def build_critic_prompt(transcript_log: list[dict], window: int = 12) -> str:
    """Baut den Prompt aus den letzten `window` Gesprächszeilen."""
    recent = transcript_log[-window:]
    lines = [f"{entry.get('who', '?')}: {entry.get('text', '')}" for entry in recent]
    return CRITIC_PROMPT_HEADER + "\n".join(lines)


def parse_critic_response(response_text: str) -> str | None:
    """Extrahiert den Hinweistext, oder None wenn alles ok ist / nichts Verwertbares kam."""
    text = (response_text or "").strip()
    if not text or text.upper().startswith("OK"):
        return None
    if text.upper().startswith("HINWEIS:"):
        hint = text.split(":", 1)[1].strip()
        return hint or None
    return None


def wrap_hint(hint: str) -> str:
    """Unsichtbare Textnachricht für die Live-Sitzung (via send_text)."""
    return (
        f"[Systemhinweis vom Hintergrund-Prüfer: {hint} Bring das beiläufig und "
        "natürlich ins Gespräch ein, in deiner aktuellen Rolle, ohne den "
        "Hintergrund-Prüfer zu erwähnen.]"
    )


class BackgroundCritic:
    """Entscheidet, wann geprüft wird, und ruft dafür ein separates Text-Modell auf."""

    def __init__(self, client: genai.Client, model: str, check_every: int = 3):
        self.client = client
        self.model = model
        self.check_every = check_every
        self._user_turns_since_check = 0

    def register_turn(self, who: str) -> bool:
        """Für jede neue Transkript-Zeile aufrufen. True = jetzt prüfen."""
        if who == "Du":
            self._user_turns_since_check += 1
        if self._user_turns_since_check >= self.check_every:
            self._user_turns_since_check = 0
            return True
        return False

    async def check(self, transcript_log: list[dict]) -> str | None:
        """Ruft das Prüf-Modell auf. Gibt den Hinweis zurück, oder None (auch bei Fehlern)."""
        prompt = build_critic_prompt(transcript_log)
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
            )
        except Exception:
            return None
        return parse_critic_response(getattr(response, "text", None) or "")
