"""
Wiederverbindungs-Regeln für die Live-Sitzung (reine Logik, kein I/O).

Eine Live-Verbindung endet aus vielen Gründen, die *kein* Grund zum
Aufgeben sind: Server kündigt per GoAway an, Sitzungsdauer erreicht,
WLAN-Wackler, kurzzeitige Server-Überlast. Andere Fehler dagegen werden
durch Wiederholen nie besser (falscher API-Key, unbekanntes Modell,
ungültige Konfiguration) -- da soll sofort eine klare Meldung kommen statt
einer endlosen Schleife.
"""
import random
from typing import Callable

# WebSocket-Close-Codes, bei denen ein neuer Versuch sinnlos ist:
# 1007 = ungültige Nutzdaten (z. B. kaputte Konfiguration),
# 1008 = Policy-Verstoß (z. B. ungültiger API-Key, Modell nicht erlaubt).
FATAL_CLOSE_CODES = {1007, 1008}
# HTTP-artige Codes: 4xx ist meist ein Fehler bei uns -- außer 408
# (Timeout) und 429 (Kontingent kurz erschöpft), die sich von selbst geben.
RETRYABLE_CLIENT_CODES = {408, 429}


def error_code(exc: BaseException) -> int | None:
    """Liest den Fehlercode aus google-genai-APIError oder websockets-Fehlern."""
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    rcvd = getattr(exc, "rcvd", None)  # websockets.ConnectionClosed
    code = getattr(rcvd, "code", None)
    return code if isinstance(code, int) else None


def is_fatal(exc: BaseException) -> bool:
    """True = Wiederholen bringt nichts, Fehler direkt melden."""
    if isinstance(exc, (ValueError, TypeError, AttributeError)):
        # Programmier-/Konfigurationsfehler, kein Netzwerkproblem
        return True
    code = error_code(exc)
    if code is None:
        return False  # z. B. OSError/Timeout: Netzwerk -> erneut versuchen
    if code in FATAL_CLOSE_CODES:
        return True
    return 400 <= code < 500 and code not in RETRYABLE_CLIENT_CODES


class ReconnectPolicy:
    """Exponentielles Backoff mit Zufallsanteil (Jitter), damit nicht alle
    Clients nach einer Server-Störung im selben Takt neu anklopfen.

    Eine Verbindung, die mindestens stable_after Sekunden gehalten hat,
    setzt den Zähler zurück -- ein Abbruch nach 20 Minuten Gespräch ist ein
    neuer Vorfall, kein „siebter Fehlversuch in Folge"."""

    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        max_attempts: int = 8,
        stable_after: float = 60.0,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.max_attempts = max_attempts
        self.stable_after = stable_after
        self._rand = rand
        self.attempts = 0

    def connection_ended(self, lasted_seconds: float) -> None:
        if lasted_seconds >= self.stable_after:
            self.attempts = 0

    def can_retry(self) -> bool:
        return self.attempts < self.max_attempts

    def next_delay(self) -> float:
        """Wartezeit vor dem nächsten Versuch; zählt den Versuch mit."""
        # Exponent deckeln: 2**1100 passt in keinen float mehr (OverflowError)
        delay = min(self.max_delay, self.base_delay * (2 ** min(self.attempts, 30)))
        self.attempts += 1
        # 50-100 % der Wartezeit (Jitter)
        return delay * (0.5 + 0.5 * self._rand())
