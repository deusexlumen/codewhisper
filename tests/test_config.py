import json

import pytest

from config import AppConfig


def write_config(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_raises_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_path = tmp_path / "config.json"
    write_config(config_path, {})
    with pytest.raises(SystemExit):
        AppConfig.load(str(config_path))


def test_load_raises_with_placeholder_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_path = tmp_path / "config.json"
    write_config(config_path, {"api_key": "HIER_DEINEN_KEY_EINTRAGEN"})
    with pytest.raises(SystemExit):
        AppConfig.load(str(config_path))


def test_load_uses_env_var_fallback(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    write_config(config_path, {})
    monkeypatch.setenv("GEMINI_API_KEY", "env-key-123")
    config = AppConfig.load(str(config_path))
    assert config.api_key == "env-key-123"


def test_load_applies_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    write_config(config_path, {"api_key": "real-key"})
    config = AppConfig.load(str(config_path))
    assert config.voice == "Aoede"
    assert config.duo_mode == "off"
    assert config.critic_enabled is False
    assert config.critic_check_every == 3
    assert config.input_device is None
    assert config.output_device is None


def test_load_reads_all_fields(tmp_path):
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        {
            "api_key": "real-key",
            "voice": "Puck",
            "duo_mode": "manual",
            "critic_enabled": True,
            "critic_model": "gemini-2.5-flash",
            "critic_check_every": 5,
            "input_device": "Mikro X",
        },
    )
    config = AppConfig.load(str(config_path))
    assert config.voice == "Puck"
    assert config.duo_mode == "manual"
    assert config.critic_enabled is True
    assert config.critic_check_every == 5
    assert config.input_device == "Mikro X"


def test_save_voice_preserves_other_fields(tmp_path):
    config_path = tmp_path / "config.json"
    write_config(config_path, {"api_key": "real-key", "model": "my-model"})
    AppConfig.save_voice("Kore", config_path=str(config_path))
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["voice"] == "Kore"
    assert data["api_key"] == "real-key"
    assert data["model"] == "my-model"


def test_save_settings_writes_multiple_fields(tmp_path):
    config_path = tmp_path / "config.json"
    write_config(config_path, {"api_key": "real-key"})
    AppConfig.save_settings(str(config_path), duo_mode="auto", voice="Charon")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["duo_mode"] == "auto"
    assert data["voice"] == "Charon"
    assert data["api_key"] == "real-key"


def test_save_settings_creates_file_if_missing(tmp_path):
    config_path = tmp_path / "config.json"
    AppConfig.save_settings(str(config_path), voice="Fenrir")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data == {"voice": "Fenrir"}
