"""GeminiLiveSession gegen einen gefälschten Live-Client: keine Netzwerk-,
keine Audio-Hardware. Prüft vor allem Verbindungs-Lebenszyklus und
Neuverbindung, nicht die echte API."""
import asyncio

import pytest
from google.genai import types

import gemini_session
from config import AppConfig
from gemini_session import GeminiLiveSession, parse_duration
from reconnect import ReconnectPolicy


class _Closed(Exception):
    def __init__(self, code):
        super().__init__(f"closed {code}")
        self.code = code


def _audio_msg(data: bytes, turn_complete: bool = False):
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            model_turn=types.Content(
                role="model",
                parts=[types.Part(inline_data=types.Blob(data=data, mime_type="audio/pcm"))],
            ),
            turn_complete=turn_complete or None,
        )
    )


def _done_msg():
    return types.LiveServerMessage(server_content=types.LiveServerContent(turn_complete=True))


class FakeSession:
    """script: Liste von Turns (Listen von Nachrichten). Danach: end_with
    (Exception -> geworfen) oder None (-> hängt, bis abgebrochen)."""

    def __init__(self, script, end_with=None):
        self.script = list(script)
        self.end_with = end_with
        self.sent_text = []

    async def receive(self):
        # Wie das echte SDK: genau EIN Turn pro receive()-Aufruf
        if not self.script:
            if self.end_with is not None:
                raise self.end_with
            await asyncio.Event().wait()
        for msg in self.script.pop(0):
            yield msg
            await asyncio.sleep(0)

    async def send_realtime_input(self, **kwargs):
        pass

    async def send_client_content(self, **kwargs):
        self.sent_text.append(kwargs)

    async def send_tool_response(self, **kwargs):
        pass


class FakeConnect:
    def __init__(self, outcome, log):
        self.outcome = outcome
        self.log = log

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.configs = []
        client = self

        class _Live:
            def connect(self, model, config):
                client.configs.append(config)
                return FakeConnect(client.outcomes.pop(0), client)

        class _Aio:
            live = _Live()

        self.aio = _Aio()


class FakeAudio:
    def __init__(self):
        self.mic_to_gemini = asyncio.Queue()
        self.gemini_to_speaker = asyncio.Queue()
        self.cleared = 0
        self.mic_cleared = 0

    def clear_playback(self):
        self.cleared += 1

    def clear_mic_queue(self):
        self.mic_cleared += 1


def _make(monkeypatch, outcomes, stop_after_connects=None, **kwargs):
    client = FakeClient(outcomes)
    monkeypatch.setattr(gemini_session.genai, "Client", lambda api_key: client)
    statuses = []
    audio = FakeAudio()
    holder = {}

    def on_status(s):
        statuses.append(s)
        if stop_after_connects and statuses.count("Verbunden") >= stop_after_connects:
            holder["s"]._stop.set()

    kwargs.setdefault("policy", ReconnectPolicy(base_delay=0, rand=lambda: 0.0))
    sess = GeminiLiveSession(AppConfig(api_key="x"), audio, on_status, **kwargs)
    holder["s"] = sess
    return sess, client, audio, statuses


async def _run(sess):
    await asyncio.wait_for(sess.run(), timeout=2)


@pytest.mark.asyncio
async def test_receive_continues_after_first_turn(monkeypatch):
    # Regression: SDK-receive() endet nach jedem Turn -- die Sitzung darf
    # deshalb nicht nach der ersten Antwort zu Ende sein.
    fake = FakeSession(
        [[_audio_msg(b"a1"), _done_msg()], [_audio_msg(b"a2"), _done_msg()]],
        end_with=_Closed(1000),
    )
    sess, _, audio, _ = _make(monkeypatch, [fake], auto_reconnect=False)
    await _run(sess)
    got = [audio.gemini_to_speaker.get_nowait() for _ in range(audio.gemini_to_speaker.qsize())]
    assert got == [b"a1", b"a2"]


@pytest.mark.asyncio
async def test_reconnects_after_network_error(monkeypatch):
    sess, client, audio, statuses = _make(
        monkeypatch,
        [OSError("weg"), FakeSession([])],
        stop_after_connects=1,
    )
    await _run(sess)
    assert len(client.configs) == 2
    assert any(s.startswith("Verbindung verloren") for s in statuses)
    assert audio.mic_cleared == 1  # veraltetes Mikro-Audio verworfen


@pytest.mark.asyncio
async def test_fatal_error_is_raised_without_retry(monkeypatch):
    sess, client, _, _ = _make(monkeypatch, [_Closed(1008), FakeSession([])])
    with pytest.raises(_Closed):
        await _run(sess)
    assert len(client.configs) == 1


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts(monkeypatch):
    sess, client, _, _ = _make(
        monkeypatch,
        [OSError("1"), OSError("2"), OSError("3")],
        policy=ReconnectPolicy(base_delay=0, max_attempts=2, rand=lambda: 0.0),
    )
    with pytest.raises(OSError):
        await _run(sess)
    assert len(client.configs) == 3


@pytest.mark.asyncio
async def test_resumption_handle_is_reused(monkeypatch):
    update = types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(
            new_handle="h1", resumable=True
        )
    )
    first = FakeSession([[update, _done_msg()]], end_with=_Closed(1011))
    sess, client, _, _ = _make(monkeypatch, [first, FakeSession([])], stop_after_connects=2)
    await _run(sess)
    assert client.configs[0].session_resumption.handle is None
    assert client.configs[1].session_resumption.handle == "h1"


@pytest.mark.asyncio
async def test_bad_resume_handle_falls_back_to_fresh_session(monkeypatch):
    update = types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(
            new_handle="alt", resumable=True
        )
    )
    first = FakeSession([[update, _done_msg()]], end_with=_Closed(1011))
    sess, client, _, _ = _make(
        monkeypatch, [first, _Closed(1008), FakeSession([])], stop_after_connects=2
    )
    await _run(sess)
    assert client.configs[1].session_resumption.handle == "alt"
    assert client.configs[2].session_resumption.handle is None


@pytest.mark.asyncio
async def test_go_away_while_idle_reconnects_with_handle(monkeypatch):
    update = types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(
            new_handle="h", resumable=True
        )
    )
    go_away = types.LiveServerMessage(go_away=types.LiveServerGoAway(time_left="10s"))
    first = FakeSession([[update, go_away]])  # hängt danach -- nur GoAway beendet
    sess, client, _, statuses = _make(
        monkeypatch, [first, FakeSession([])], stop_after_connects=2
    )
    await _run(sess)
    assert len(client.configs) == 2
    assert "Verbinde neu …" in statuses
    assert sess.policy.attempts == 0  # kein Fehlversuch gezählt


@pytest.mark.asyncio
async def test_resumption_can_be_disabled(monkeypatch):
    sess, client, _, _ = _make(monkeypatch, [FakeSession([], end_with=_Closed(1000))], auto_reconnect=False)
    sess.config.session_resumption = False
    await _run(sess)
    assert client.configs[0].session_resumption is None
    assert client.configs[0].context_window_compression is None


@pytest.mark.asyncio
async def test_send_text_never_raises(monkeypatch):
    sess, _, _, _ = _make(monkeypatch, [])
    assert await sess.send_text("hallo") is False  # nicht verbunden

    class Broken:
        async def send_client_content(self, **kwargs):
            raise _Closed(1006)

    sess.session = Broken()
    assert await sess.send_text("hallo") is False


@pytest.mark.asyncio
async def test_send_text_does_not_interrupt_by_default(monkeypatch):
    sess, _, _, _ = _make(monkeypatch, [])
    fake = FakeSession([])
    sess.session = fake
    assert await sess.send_text("hinweis") is True
    assert fake.sent_text[0]["turn_complete"] is False


def test_parse_duration():
    assert parse_duration("10s") == 10.0
    assert parse_duration("1.5s") == 1.5
    assert parse_duration(None) is None
    assert parse_duration("bogus") is None
