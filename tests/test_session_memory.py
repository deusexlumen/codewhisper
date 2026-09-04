import pytest

from session_memory import build_resume_message, build_summary_prompt, summarize_session


# ---------- build_summary_prompt ----------

def test_build_summary_prompt_includes_entries():
    records = [
        {"who": "Du", "text": "Wir bauen einen Sprach-Assistenten."},
        {"who": "KI", "text": "Klingt gut, welche Phase als Erstes?"},
    ]
    prompt = build_summary_prompt(records)
    assert "Du: Wir bauen einen Sprach-Assistenten." in prompt
    assert "KI: Klingt gut, welche Phase als Erstes?" in prompt


def test_build_summary_prompt_handles_missing_keys():
    prompt = build_summary_prompt([{}])
    assert "?: " in prompt


# ---------- build_resume_message ----------

def test_build_resume_message_contains_summary_and_marker():
    msg = build_resume_message("Es ging um die Preisstrategie.")
    assert "Es ging um die Preisstrategie." in msg
    assert msg.startswith("[Systemhinweis")


# ---------- summarize_session (async, fake client) ----------

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
async def test_summarize_session_returns_summary_text():
    client = _FakeClient("Es ging um die Preisstrategie.")
    result = await summarize_session(
        client, "gemini-2.5-flash", [{"who": "Du", "text": "Reden wir über Preise."}]
    )
    assert result == "Es ging um die Preisstrategie."
    assert client.aio.models.calls[0][0] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_summarize_session_returns_none_for_empty_records():
    client = _FakeClient("sollte nie aufgerufen werden")
    result = await summarize_session(client, "gemini-2.5-flash", [])
    assert result is None
    assert client.aio.models.calls == []


@pytest.mark.asyncio
async def test_summarize_session_returns_none_for_blank_response():
    client = _FakeClient("   ")
    result = await summarize_session(
        client, "gemini-2.5-flash", [{"who": "Du", "text": "Hallo"}]
    )
    assert result is None


@pytest.mark.asyncio
async def test_summarize_session_swallows_network_errors():
    client = _FakeClient(text=None, raise_error=True)
    result = await summarize_session(
        client, "gemini-2.5-flash", [{"who": "Du", "text": "Hallo"}]
    )
    assert result is None
