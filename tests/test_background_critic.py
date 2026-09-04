import pytest

from background_critic import (
    BackgroundCritic,
    build_critic_prompt,
    extract_last_hint,
    parse_critic_response,
    wrap_hint,
)


# ---------- build_critic_prompt ----------

def test_build_critic_prompt_includes_all_entries():
    transcript = [
        {"who": "Du", "text": "Wir verkaufen an alle Studenten weltweit."},
        {"who": "KI", "text": "Klingt spannend, wie viele sind das?"},
    ]
    prompt = build_critic_prompt(transcript)
    assert "Du: Wir verkaufen an alle Studenten weltweit." in prompt
    assert "KI: Klingt spannend, wie viele sind das?" in prompt


def test_build_critic_prompt_limits_to_window():
    transcript = [{"who": "Du", "text": f"Zeile {i}"} for i in range(20)]
    prompt = build_critic_prompt(transcript, window=5)
    assert "Zeile 19" in prompt
    assert "Zeile 15" in prompt
    assert "Zeile 14" not in prompt


def test_build_critic_prompt_handles_missing_keys():
    prompt = build_critic_prompt([{}])
    assert "?: " in prompt


def test_build_critic_prompt_without_code_snippet_omits_code_section():
    prompt = build_critic_prompt([{"who": "Du", "text": "Hallo"}])
    assert "Code-Ausschnitt aus der Zwischenablage:" not in prompt


def test_build_critic_prompt_includes_code_snippet():
    prompt = build_critic_prompt(
        [{"who": "Du", "text": "Ist das jetzt gefixt?"}],
        code_snippet="def foo():\n    return 1 / 0",
    )
    assert "Code-Ausschnitt" in prompt
    assert "def foo():\n    return 1 / 0" in prompt


def test_build_critic_prompt_ignores_empty_code_snippet():
    prompt = build_critic_prompt([{"who": "Du", "text": "Hallo"}], code_snippet="   ")
    assert "Code-Ausschnitt aus der Zwischenablage:" not in prompt


def test_build_critic_prompt_includes_prior_hint():
    prompt = build_critic_prompt(
        [{"who": "Du", "text": "Weiter geht's."}],
        prior_hint="Die Zielgruppe widersprach sich.",
    )
    assert "Offener Punkt aus letzter Sitzung:" in prompt
    assert "Die Zielgruppe widersprach sich." in prompt


def test_build_critic_prompt_without_prior_hint_omits_section():
    prompt = build_critic_prompt([{"who": "Du", "text": "Hallo"}])
    assert "Offener Punkt aus letzter Sitzung:" not in prompt


# ---------- extract_last_hint ----------

def test_extract_last_hint_returns_last_kritiker_entry():
    records = [
        {"who": "Du", "text": "Reden wir über Preise."},
        {"who": "Kritiker", "text": "Erster Hinweis."},
        {"who": "KI", "text": "Verstanden."},
        {"who": "Kritiker", "text": "Zweiter, neuerer Hinweis."},
    ]
    assert extract_last_hint(records) == "Zweiter, neuerer Hinweis."


def test_extract_last_hint_returns_none_when_no_kritiker_entries():
    records = [{"who": "Du", "text": "Hallo"}, {"who": "KI", "text": "Hi"}]
    assert extract_last_hint(records) is None


def test_extract_last_hint_returns_none_for_empty_records():
    assert extract_last_hint([]) is None


# ---------- parse_critic_response ----------

@pytest.mark.parametrize(
    "response",
    ["OK", "ok", "  OK  ", "OK.", ""],
)
def test_parse_critic_response_ok_variants_return_none(response):
    assert parse_critic_response(response) is None


def test_parse_critic_response_extracts_hint():
    hint = parse_critic_response("HINWEIS: Die Zielgruppe widerspricht sich.")
    assert hint == "Die Zielgruppe widerspricht sich."


def test_parse_critic_response_case_insensitive_prefix():
    hint = parse_critic_response("hinweis: kleine Schreibweise")
    assert hint == "kleine Schreibweise"


def test_parse_critic_response_empty_hint_returns_none():
    assert parse_critic_response("HINWEIS:") is None
    assert parse_critic_response("HINWEIS:   ") is None


def test_parse_critic_response_garbage_returns_none():
    assert parse_critic_response("irgendein unerwarteter Text") is None
    assert parse_critic_response(None) is None


# ---------- wrap_hint ----------

def test_wrap_hint_contains_original_hint_and_is_invisible_marker():
    wrapped = wrap_hint("Die Kostenannahme ist zu niedrig.")
    assert "Die Kostenannahme ist zu niedrig." in wrapped
    assert wrapped.startswith("[Systemhinweis")


# ---------- BackgroundCritic.register_turn ----------

def test_register_turn_triggers_after_n_user_turns():
    critic = BackgroundCritic(client=None, model="x", check_every=3)
    assert critic.register_turn("Du") is False
    assert critic.register_turn("KI") is False  # KI-Zeilen zählen nicht
    assert critic.register_turn("Du") is False
    assert critic.register_turn("Du") is True  # 3. "Du"-Zeile -> jetzt prüfen


def test_register_turn_resets_counter_after_trigger():
    critic = BackgroundCritic(client=None, model="x", check_every=2)
    critic.register_turn("Du")
    assert critic.register_turn("Du") is True
    assert critic.register_turn("Du") is False
    assert critic.register_turn("Du") is True


# ---------- BackgroundCritic.check (async, mit Fake-Client) ----------

class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeAioModels:
    def __init__(self, text, raise_error=False):
        self._text = text
        self._raise_error = raise_error
        self.calls = []

    async def generate_content(self, *, model, contents):
        self.calls.append((model, contents))
        if self._raise_error:
            raise RuntimeError("Netzwerkfehler")
        return _FakeResponse(self._text)


class _FakeAio:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, text, raise_error=False):
        self.aio = _FakeAio(_FakeAioModels(text, raise_error))


@pytest.mark.asyncio
async def test_check_returns_hint_when_critic_finds_issue():
    client = _FakeClient("HINWEIS: Die Marge geht nicht auf.")
    critic = BackgroundCritic(client=client, model="gemini-2.5-flash")
    result = await critic.check([{"who": "Du", "text": "Wir verkaufen für 1 Euro Kosten von 2 Euro."}])
    assert result == "Die Marge geht nicht auf."
    assert client.aio.models.calls[0][0] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_check_returns_none_when_ok():
    client = _FakeClient("OK")
    critic = BackgroundCritic(client=client, model="gemini-2.5-flash")
    result = await critic.check([{"who": "Du", "text": "Alles plausibel."}])
    assert result is None


@pytest.mark.asyncio
async def test_check_swallows_network_errors():
    client = _FakeClient(text=None, raise_error=True)
    critic = BackgroundCritic(client=client, model="gemini-2.5-flash")
    result = await critic.check([{"who": "Du", "text": "Egal was."}])
    assert result is None


@pytest.mark.asyncio
async def test_check_threads_code_snippet_into_prompt():
    client = _FakeClient("OK")
    critic = BackgroundCritic(client=client, model="gemini-2.5-flash")
    await critic.check(
        [{"who": "Du", "text": "Hab den Bug gefixt."}],
        code_snippet="def foo():\n    return 1 / 0",
    )
    sent_prompt = client.aio.models.calls[0][1]
    assert "def foo():\n    return 1 / 0" in sent_prompt


@pytest.mark.asyncio
async def test_check_threads_prior_hint_into_prompt():
    client = _FakeClient("OK")
    critic = BackgroundCritic(client=client, model="gemini-2.5-flash")
    await critic.check(
        [{"who": "Du", "text": "Weiter geht's."}],
        prior_hint="Die Zielgruppe widersprach sich.",
    )
    sent_prompt = client.aio.models.calls[0][1]
    assert "Die Zielgruppe widersprach sich." in sent_prompt
