import sessions


def test_list_sessions_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "does-not-exist")
    assert sessions.list_sessions() == []


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    transcript = [
        {"who": "Du", "text": "Hallo"},
        {"who": "KI", "text": "Hi, wie kann ich helfen?"},
    ]
    path = sessions.save_session(transcript)
    assert path.exists()
    loaded = sessions.load_session(path)
    assert loaded == transcript


def test_list_sessions_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    sessions.SESSIONS_DIR.mkdir()
    older = sessions.SESSIONS_DIR / "2020-01-01_00-00-00.json"
    newer = sessions.SESSIONS_DIR / "2030-01-01_00-00-00.json"
    older.write_text("[]", encoding="utf-8")
    newer.write_text("[]", encoding="utf-8")
    found = sessions.list_sessions()
    assert found[0] == newer
    assert found[1] == older


def test_save_session_preserves_umlauts(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    transcript = [{"who": "Du", "text": "Können wir über Ideen sprechen?"}]
    path = sessions.save_session(transcript)
    loaded = sessions.load_session(path)
    assert loaded[0]["text"] == "Können wir über Ideen sprechen?"
