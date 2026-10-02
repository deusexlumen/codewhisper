"""
Verbindung zur Gemini Live API.

Öffnet eine dauerhafte Zwei-Wege-Verbindung: Mikro-Audio geht hoch,
Antwort-Audio und Textmitschrift kommen runter.

Robustheit: Bricht die Verbindung ab (Netzwerk, Server-GoAway, Ende der
Sitzungsdauer), baut run() sie selbstständig neu auf -- mit exponentiellem
Backoff (reconnect.py) und, wenn der Server einen Resumption-Handle
geliefert hat, mit erhaltenem Gesprächskontext. Nur Fehler, die durch
Wiederholen nicht besser werden (API-Key, Modellname, Konfiguration),
gehen sofort nach oben.
"""
import asyncio
import logging
import re
from typing import TYPE_CHECKING, Callable

from google import genai
from google.genai import types

import dev_tools
from config import AppConfig
from reconnect import ReconnectPolicy, is_fatal

if TYPE_CHECKING:
    # Nur für den Typ-Hinweis: sounddevice/PortAudio soll nicht geladen
    # werden müssen, wenn die Discord-Bridge statt AudioEngine einen
    # DiscordAudioAdapter mit derselben Schnittstelle übergibt.
    from audio_engine import AudioEngine

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
TranscriptCallback = Callable[[str, str], None]  # (wer, text)
ToolCallCallback = Callable[[str, str], None]  # (Kommando, Ergebnis-Text)

# Bei GoAway so viele Sekunden vor Fristende spätestens neu verbinden
GO_AWAY_MARGIN = 2.0


def _is_normal_close(exc: BaseException) -> bool:
    """Erkennt ein normales Ende der Verbindung (Code 1000 = „alles ok, tschüss").
    Das ist kein Fehler und soll nicht als einer gemeldet werden."""
    if type(exc).__name__ == "ConnectionClosedOK":
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 1000


def parse_duration(text: str | None) -> float | None:
    """„10s" / „1.5s" (protobuf-Duration als String) -> Sekunden."""
    if not text:
        return None
    match = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*s\s*", text)
    return float(match.group(1)) if match else None


class GeminiLiveSession:
    def __init__(
        self,
        config: AppConfig,
        audio: "AudioEngine",
        on_status: StatusCallback,
        on_transcript: TranscriptCallback | None = None,
        on_tool_call: ToolCallCallback | None = None,
        enable_tools: bool = True,
        auto_reconnect: bool = True,
        policy: ReconnectPolicy | None = None,
    ):
        self.config = config
        self.audio = audio
        self.on_status = on_status
        self.on_transcript = on_transcript
        self.on_tool_call = on_tool_call
        # Discord-Bridge schaltet das ab: dort könnte jede Person im Kanal
        # pytest/git auf dem Host-Rechner auslösen.
        self.enable_tools = enable_tools
        self.auto_reconnect = auto_reconnect
        self.policy = policy or ReconnectPolicy()
        self.session = None  # aktive Verbindung (für spätere Text-Einspeisung)
        self._tool_tasks: dict[str, asyncio.Task] = {}  # laufende Tool-Aufrufe je call.id
        self._stop = asyncio.Event()
        self._reconnect_now = asyncio.Event()  # GoAway: geplanter Neuaufbau
        self._go_away_pending = False
        self._model_speaking = False
        self._resume_handle: str | None = None
        self._go_away_timer: asyncio.TimerHandle | None = None
        self._connected = False  # hat die aktuelle Verbindung „Verbunden" erreicht?

    async def stop(self) -> None:
        self._stop.set()

    # ---------- Verbindungs-Lebenszyklus ----------

    def _build_config(self) -> types.LiveConnectConfig:
        extras = {}
        if self.config.session_resumption:
            # Resumption: Server schickt laufend Handles, mit denen eine neue
            # Verbindung den bisherigen Kontext übernimmt. Kompression: ältere
            # Gesprächsteile werden gleitend verdichtet, damit lange
            # Sitzungen nicht am Kontext-/Dauerlimit enden.
            extras["session_resumption"] = types.SessionResumptionConfig(
                handle=self._resume_handle
            )
            extras["context_window_compression"] = types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow()
            )
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=self.config.system_instruction)]
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.config.voice
                    )
                )
            ),
            # Mitschrift beider Seiten, damit das Fenster Text zeigen kann
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # Function-Calling: feste Allowlist (dev_tools.py), keine freien
            # Kommandos -- das Modell wählt nur einen Namen aus einem Enum.
            tools=(
                [types.Tool(function_declarations=[dev_tools.build_tool_declaration()])]
                if self.enable_tools
                else None
            ),
            **extras,
        )

    async def run(self) -> None:
        """Hält die Verbindung, bis stop() gerufen wird (oder ein nicht
        behebbarer Fehler auftritt -- der wird dann geworfen)."""
        client = genai.Client(api_key=self.config.api_key)
        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            started = loop.time()
            used_handle = self._resume_handle is not None
            error: BaseException | None = None
            go_away = False
            try:
                go_away = await self._run_once(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not self.auto_reconnect:
                    raise
                if used_handle and not self._connected:
                    # Abgelaufener/ungültiger Resumption-Handle: ohne ihn neu
                    # versuchen statt aufzugeben (Kontext geht dann verloren).
                    log.warning("Wiederaufnahme fehlgeschlagen (%s), starte frisch", exc)
                    self._resume_handle = None
                elif is_fatal(exc):
                    raise
                error = exc
            finally:
                self.session = None
                self._cancel_tool_tasks()
                if self._go_away_timer is not None:
                    # Sonst löst ein alter GoAway-Timer später den Neuaufbau
                    # der *nächsten* Verbindung aus.
                    self._go_away_timer.cancel()
                    self._go_away_timer = None

            if self._stop.is_set() or not self.auto_reconnect:
                return

            self.policy.connection_ended(loop.time() - started)
            if go_away and self._resume_handle:
                # Angekündigtes Ende mit Handle: sofort nahtlos weiter
                self.on_status("Verbinde neu …")
                continue
            if not self.policy.can_retry():
                if error is not None:
                    raise error
                raise ConnectionError(
                    f"Verbindung nach {self.policy.attempts} Versuchen nicht wiederherstellbar."
                )
            delay = self.policy.next_delay()
            reason = f" ({error})" if error is not None else ""
            log.warning("Live-Verbindung beendet%s, neuer Versuch in %.1f s", reason, delay)
            self.on_status(f"Verbindung verloren – neuer Versuch in {delay:.0f} s …")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _run_once(self, client) -> bool:
        """Eine einzelne Verbindung. -> True, wenn sie wegen GoAway endete."""
        self._connected = False
        self._reconnect_now.clear()
        self._go_away_pending = False
        self._model_speaking = False
        self.on_status("Verbinde …")
        async with client.aio.live.connect(
            model=self.config.model, config=self._build_config()
        ) as session:
            self.session = session
            self._connected = True
            # Was während der Verbindungspause ins Mikro kam, ist veraltet
            clear_mic = getattr(self.audio, "clear_mic_queue", None)
            if clear_mic:
                clear_mic()
            self.on_status("Verbunden")
            send_task = asyncio.create_task(self._send_loop(session))
            recv_task = asyncio.create_task(self._receive_loop(session))
            stop_task = asyncio.create_task(self._stop.wait())
            goaway_task = asyncio.create_task(self._reconnect_now.wait())
            tasks = {send_task, recv_task, stop_task, goaway_task}
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # Cancelled Tasks sauber abholen, sonst „exception was never retrieved"
            await asyncio.gather(*pending, return_exceptions=True)
            # Echte Fehler nach oben geben, normale Verbindungs-Enden ignorieren
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and not _is_normal_close(exc):
                    raise exc
            return goaway_task in done

    async def _send_loop(self, session) -> None:
        """Schickt Mikro-Audio Stück für Stück an Gemini. Ein str in der
        Warteschlange (Discord-Sprecherwechsel) geht als Echtzeit-Text raus --
        send_realtime_input(text=) beendet, anders als send_client_content,
        den laufenden Nutzer-Turn nicht."""
        try:
            while True:
                chunk = await self.audio.mic_to_gemini.get()
                if isinstance(chunk, str):
                    await session.send_realtime_input(text=chunk)
                    continue
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
        except Exception as exc:
            if not _is_normal_close(exc):
                raise

    async def _receive_loop(self, session) -> None:
        """Empfängt Antworten: Audio abspielen, Text ins Fenster, Tool-Aufrufe ausführen.

        Wichtig: session.receive() im google-genai-SDK *endet nach jedem
        abgeschlossenen KI-Turn* (turn_complete). Ohne die äußere Schleife
        wäre die Verbindung nach der ersten Antwort vorbei. Ist die
        Verbindung wirklich zu, wirft receive() -- dann endet auch die
        Schleife (kein Leerlauf-Kreisen)."""
        try:
            while True:
                async for message in session.receive():
                    self._dispatch(session, message)
        except Exception as exc:
            if not _is_normal_close(exc):
                raise

    def _dispatch(self, session, message) -> None:
        update = message.session_resumption_update
        if update and update.resumable and update.new_handle:
            self._resume_handle = update.new_handle

        if message.go_away:
            self._schedule_go_away(parse_duration(message.go_away.time_left))

        if message.tool_call:
            self._start_tool_calls(session, message.tool_call)

        if message.tool_call_cancellation:
            for call_id in message.tool_call_cancellation.ids or []:
                task = self._tool_tasks.pop(call_id, None)
                if task:
                    task.cancel()

        self._handle_message(message)

    def _schedule_go_away(self, time_left: float | None) -> None:
        """Server kündigt das Verbindungsende an. Nicht mitten in eine
        Antwort hineinschneiden: neu verbinden, sobald die KI fertig
        gesprochen hat -- spätestens aber kurz vor Fristende."""
        log.info("GoAway vom Server, verbleibend: %s s", time_left)
        self._go_away_pending = True
        if not self._model_speaking:
            self._reconnect_now.set()
            return
        if time_left is not None:
            delay = max(0.0, time_left - GO_AWAY_MARGIN)
            if self._go_away_timer is not None:
                self._go_away_timer.cancel()
            self._go_away_timer = asyncio.get_running_loop().call_later(
                delay, self._reconnect_now.set
            )

    # ---------- Function-Calling ----------

    def _start_tool_calls(self, session, tool_call) -> None:
        """Nicht abwarten: gemini-3.8-live ruft Tools asynchron (NON_BLOCKING)
        und spricht währenddessen weiter. Ein await im Empfangs-Loop würde
        dessen Audio bis zu 30 s (dev_tools-Timeout) anhalten. Ein Task je
        Aufruf, damit tool_call_cancellation gezielt einzelne abbrechen kann."""
        for call in tool_call.function_calls or []:
            task = asyncio.create_task(self._run_tool_call(session, call))
            key = call.id or str(id(task))
            self._tool_tasks[key] = task
            task.add_done_callback(lambda _t, k=key: self._tool_tasks.pop(k, None))

    async def _run_tool_call(self, session, call) -> None:
        """Führt ein Allowlist-Kommando aus (dev_tools.run_tool wirft selbst
        nie) und schickt das Ergebnis zurück. Ein fehlschlagender Tool-Aufruf
        oder eine inzwischen geschlossene Verbindung darf die Sprach-Sitzung
        nie zum Absturz bringen (gleiches Prinzip wie BackgroundCritic.check())."""
        command = (call.args or {}).get("command", "")
        try:
            result_text = await dev_tools.run_tool(command)
        except Exception as exc:
            result_text = f"Kommando konnte nicht ausgeführt werden: {exc}"
        if self.on_tool_call:
            try:
                self.on_tool_call(command, result_text)
            except Exception:
                log.exception("on_tool_call-Callback fehlgeschlagen")
        try:
            await session.send_tool_response(
                function_responses=[
                    types.FunctionResponse(
                        id=call.id,
                        name=call.name,
                        response={"output": result_text},
                        # Ergebnis erst einbringen, wenn die KI ihren Satz zu
                        # Ende gesprochen hat, statt sie mittendrin abzuschneiden.
                        scheduling=types.FunctionResponseScheduling.WHEN_IDLE,
                    )
                ]
            )
        except Exception as exc:
            log.warning("Tool-Antwort konnte nicht gesendet werden: %s", exc)

    def _cancel_tool_tasks(self) -> None:
        for task in list(self._tool_tasks.values()):
            task.cancel()
        self._tool_tasks.clear()

    # ---------- Server-Inhalte ----------

    def _handle_message(self, message) -> None:
        content = message.server_content
        if content is None:
            return

        if content.interrupted:
            # Nutzer hat die KI unterbrochen -> Rest der Antwort löschen
            self.audio.clear_playback()
            self.on_status("Verbunden (unterbrochen)")

        if content.model_turn:
            self._model_speaking = True
            for part in content.model_turn.parts or []:
                if part.inline_data and part.inline_data.data:
                    self.audio.gemini_to_speaker.put_nowait(
                        part.inline_data.data
                    )

        if content.input_transcription and content.input_transcription.text:
            if self.on_transcript:
                self.on_transcript("Du", content.input_transcription.text)

        if content.output_transcription and content.output_transcription.text:
            if self.on_transcript:
                self.on_transcript("KI", content.output_transcription.text)

        if content.turn_complete or content.interrupted:
            self._model_speaking = False
            if self._go_away_pending:
                self._reconnect_now.set()

    async def send_text(self, text: str, interrupt: bool = False) -> bool:
        """Schiebt eine unsichtbare Textnachricht in die laufende Sitzung
        (Kritiker-Hinweis, Rollenwechsel, Code-Kontext, Sitzungs-Zusammenfassung).
        Gibt False zurück, wenn gerade keine Verbindung besteht oder das
        Senden scheitert (z. B. mitten im Neuaufbau) -- nie eine Exception,
        weil die Aufrufer das als Hintergrund-Task feuern.

        Bei gemini-3.8-live bricht turn_complete=True eine laufende Antwort
        *immer* ab. Ohne turn_complete wartet der Server dagegen auf die
        nächste Nachricht (z. B. das nächste Gesagte) und berücksichtigt den
        Hinweis dann -- genau das „beiläufig einbringen", das alle bisherigen
        Hinweistexte verlangen. Deshalb standardmäßig nicht unterbrechen."""
        session = self.session
        if session is None:
            return False
        try:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]),
                turn_complete=interrupt,
            )
            return True
        except Exception as exc:
            log.warning("Text-Hinweis konnte nicht gesendet werden: %s", exc)
            return False
