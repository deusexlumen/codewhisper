"""Liest die Einstellungen aus config.json (oder Umgebungsvariable)."""
import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    api_key: str
    model: str = "gemini-live-2.5-flash-preview"
    voice: str = "Aoede"
    system_instruction: str = "Du bist ein hilfreicher Assistent. Antworte auf Deutsch, kurz und klar."
    input_device: str | None = None
    output_device: str | None = None
    # Phase 3 – Duo-Mode: "off" | "auto" (nach jeder Antwort) | "manual" (per Knopf)
    duo_mode: str = "off"
    # Phase 4 – Hintergrund-Prüfer (Closed-Loop)
    critic_enabled: bool = False
    critic_model: str = "gemini-2.5-flash"
    critic_check_every: int = 3

    @classmethod
    def load(cls, config_path: str = "config.json") -> "AppConfig":
        data: dict = {}
        path = Path(config_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))

        api_key = data.get("api_key") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key or api_key == "HIER_DEINEN_KEY_EINTRAGEN":
            raise SystemExit(
                "Kein API-Key gefunden. Bitte config.example.json nach config.json "
                "kopieren und dort den Key eintragen (siehe README.md)."
            )

        return cls(
            api_key=api_key,
            model=data.get("model", cls.model),
            voice=data.get("voice", cls.voice),
            system_instruction=data.get("system_instruction", cls.system_instruction),
            input_device=data.get("input_device") or None,
            output_device=data.get("output_device") or None,
            duo_mode=data.get("duo_mode", cls.duo_mode),
            critic_enabled=data.get("critic_enabled", cls.critic_enabled),
            critic_model=data.get("critic_model", cls.critic_model),
            critic_check_every=data.get("critic_check_every", cls.critic_check_every),
        )

    @staticmethod
    def save_settings(config_path: str = "config.json", **fields) -> None:
        """Schreibt einzelne Felder in config.json, ohne die anderen anzufassen."""
        path = Path(config_path)
        data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.update(fields)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def save_voice(voice: str, config_path: str = "config.json") -> None:
        """Wirkt erst beim nächsten Verbindungsaufbau (Stimme wird nur beim Connect gesetzt)."""
        AppConfig.save_settings(config_path, voice=voice)
