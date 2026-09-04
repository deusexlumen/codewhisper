"""Function-Calling: die KI kann ein festes, sicheres Kommando ausführen
(Tests laufen lassen, Git-Status/Diff/Log ansehen) statt nur darüber zu reden
-- der nächste Schritt nach Code-Kontext-Grounding (Sitzung 1) und dem
Kritiker, der gegen echten Code prüft (Sitzung 2): jetzt lässt sich die
Behauptung "die Tests sind grün" tatsächlich verifizieren.

Sicherheit: Das Modell übergibt IMMER nur einen Namen aus `ALLOWED` (per
Enum in der Function-Declaration erzwungen) -- nie eigene Argumente oder
freien Text. Der tatsächliche `argv` kommt ausschließlich aus dieser Datei.
Keine Schreib-/Löschoperationen in der Allowlist. "pytest" ist NICHT rein
lesend (führt Repo-Testcode aus), aber destruktionsfrei.

Läuft über `asyncio.create_subprocess_exec` (nie `subprocess.run`/
`shell=True`), damit ein hängender Prozess (z.B. ein Endlos-Test) den
Event-Loop -- und damit Mikro/Lautsprecher -- nicht blockiert; ein Timeout
killt den Prozess statt ihn im Hintergrund weiterlaufen zu lassen.

Ein Exit-Code != 0 (z.B. fehlgeschlagene Tests) ist eine valide Antwort,
kein Fehler -- anders als bei `BackgroundCritic.check()`/`code_context`
wird hier NICHT stillschweigend `None` zurückgegeben, sondern immer ein
sprechbarer Text. Nur echte Ausführungsprobleme (unbekannter Name, Timeout,
Programm fehlt) liefern eine Fehlermeldung statt Kommando-Ausgabe.
"""
import asyncio
from typing import Awaitable, Callable, Optional

MAX_OUTPUT_CHARS = 4000
DEFAULT_TIMEOUT = 30.0

TOOL_NAME = "run_dev_command"

ALLOWED: dict[str, list[str]] = {
    "pytest": ["pytest", "-q"],
    "git_status": ["git", "status"],
    "git_diff": ["git", "diff"],
    "git_log": ["git", "log", "-n", "5", "--oneline"],
}

Runner = Callable[[list[str], Optional[str], float], Awaitable[tuple[int, str]]]


def build_tool_declaration() -> dict:
    """Baut die Function-Declaration fürs Live-API `tools`-Feld. Der einzige
    Parameter ist ein Enum aus den ALLOWED-Namen -- das Modell kann so nie
    eigene Argumente durchreichen, nur einen der festen Namen wählen."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Führt ein festes, sicheres Entwickler-Kommando im Projektordner "
            "aus (Tests laufen lassen, Git-Status/Diff/Log ansehen) und gibt "
            "die Ausgabe zurück. Keine freien Kommandos, keine Schreib- oder "
            "Löschoperationen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "enum": sorted(ALLOWED.keys()),
                    "description": "Welches Kommando ausgeführt werden soll.",
                }
            },
            "required": ["command"],
        },
    }


def format_result(
    name: str, exit_code: int, output: str, max_chars: int = MAX_OUTPUT_CHARS
) -> str:
    """Baut den Text, der als Function-Response zurückgeht -- lange Ausgabe
    wird gekürzt (gleiches Prinzip wie `code_context.build_context_message`)."""
    text = (output or "").strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    note = " (gekürzt)" if truncated else ""
    status = "erfolgreich" if exit_code == 0 else f"mit Exit-Code {exit_code}"
    body = f"\n\n{text}" if text else ""
    return f"Kommando '{name}' {status} beendet{note}.{body}"


async def _default_runner(argv: list[str], cwd: Optional[str], timeout: float) -> tuple[int, str]:
    """Echte Ausführung via asyncio-Subprozess (blockiert den Event-Loop nicht).
    Killt den Prozess bei Zeitüberschreitung, statt ihn hängen zu lassen."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    return process.returncode, stdout.decode("utf-8", errors="replace")


async def run_tool(
    name: str,
    runner: Optional[Runner] = None,
    cwd: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Führt ein Allowlist-Kommando aus. Gibt IMMER einen sprechbaren Text
    zurück (nie None) -- `runner` ist injizierbar für Tests, analog zu
    `code_context.read_clipboard(paste_fn=...)`."""
    if name not in ALLOWED:
        return f"Unbekanntes Kommando '{name}' -- nicht in der Allowlist."
    argv = ALLOWED[name]
    if runner is None:
        runner = _default_runner
    try:
        exit_code, output = await runner(argv, cwd, timeout)
    except asyncio.TimeoutError:
        return f"Kommando '{name}' abgebrochen -- Zeitlimit ({timeout:.0f}s) überschritten."
    except Exception as exc:
        return f"Kommando '{name}' konnte nicht ausgeführt werden: {exc}"
    return format_result(name, exit_code, output)
