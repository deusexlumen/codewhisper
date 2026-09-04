"""Gesprächs-Sitzungen speichern und wieder laden.

Speichert den Transkript-Verlauf (wer hat was gesagt) als JSON-Datei im
Ordner `sessions/`. Beim Laden wird nur der Text angezeigt — die KI hat
danach keine Erinnerung an das alte Gespräch, es ist ein reines Nachlesen.
"""
import json
from datetime import datetime
from pathlib import Path

SESSIONS_DIR = Path("sessions")


def save_session(transcript: list[dict]) -> Path:
    """Speichert den Verlauf unter einem Zeitstempel-Dateinamen."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = SESSIONS_DIR / f"{timestamp}.json"
    path.write_text(
        json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def list_sessions() -> list[Path]:
    """Gefundene Sitzungen, neueste zuerst."""
    if not SESSIONS_DIR.exists():
        return []
    return sorted(SESSIONS_DIR.glob("*.json"), reverse=True)


def load_session(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))
