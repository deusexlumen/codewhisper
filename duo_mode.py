"""Phase 3 – Die zwei Denkrollen (Duo-Mode).

Kein Verbindungswechsel für den Rollenwechsel (siehe README, Entscheidung 1):
die KI bekommt eine einzige `system_instruction`, die ihr beibringt, wie sie
zwischen Visionär und Pragmatiker wechselt. Zwei Modi:

- "auto":   die KI wechselt selbstständig nach jeder eigenen Antwort.
- "manual": die KI bleibt in einer Rolle, bis eine unsichtbare Textnachricht
            (über `GeminiLiveSession.send_text()`) sie zum Wechsel auffordert.
"""

ROLE_VISIONAER = "Visionär"
ROLE_PRAGMATIKER = "Pragmatiker"

MODES = ("off", "auto", "manual")

_ROLE_DESCRIPTIONS = (
    f"- **{ROLE_VISIONAER}**: denkt groß, sieht Chancen, ist enthusiastisch und "
    "denkt in Möglichkeiten statt Einschränkungen.\n"
    f"- **{ROLE_PRAGMATIKER}**: prüft auf Realismus, Kosten, Aufwand und Risiken, "
    "stellt kritische Rückfragen."
)


def next_role(current_role: str) -> str:
    """Gibt die jeweils andere Rolle zurück."""
    return ROLE_PRAGMATIKER if current_role == ROLE_VISIONAER else ROLE_VISIONAER


def build_system_instruction(base_instruction: str, mode: str, start_role: str = ROLE_VISIONAER) -> str:
    """Erweitert die Basis-Instruktion um die Duo-Mode-Regeln.

    mode == "off" gibt die Basis-Instruktion unverändert zurück.
    """
    if mode not in MODES:
        raise ValueError(f"Unbekannter Duo-Mode: {mode!r} (erlaubt: {MODES})")
    if mode == "off":
        return base_instruction

    if mode == "auto":
        switch_rule = (
            "Wechsle nach JEDER deiner Antworten automatisch die Rolle "
            "(erst Visionär, dann Pragmatiker, dann wieder Visionär, usw.). "
            "Kündige den Rollenwechsel kurz an, z.B. 'Als Pragmatiker sehe ich das so:'."
        )
    else:  # manual
        switch_rule = (
            f"Du beginnst als {start_role}. Bleibe in dieser Rolle, bis du eine "
            "Systemnachricht bekommst, die dich explizit zum Rollenwechsel auffordert. "
            "Kündige einen Rollenwechsel kurz an, z.B. 'Als Pragmatiker sehe ich das so:'."
        )

    return (
        f"{base_instruction}\n\n"
        "Zusätzlich: Du spielst abwechselnd zwei Denkrollen im selben Gespräch:\n"
        f"{_ROLE_DESCRIPTIONS}\n"
        f"{switch_rule}"
    )


def build_switch_message(role: str) -> str:
    """Unsichtbare Textnachricht für den manuellen Rollenwechsel (via send_text)."""
    return (
        f"[Systemhinweis: Wechsle jetzt in die Rolle {role}. Antworte ab jetzt "
        f"ausschließlich als {role}, bis du erneut dazu aufgefordert wirst. "
        "Bestätige den Wechsel kurz in deiner nächsten Antwort.]"
    )
