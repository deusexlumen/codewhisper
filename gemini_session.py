"""
Verbindung zur Gemini Live API.

Öffnet eine dauerhafte Zwei-Wege-Verbindung: Mikro-Audio geht hoch,
Antwort-Audio und Textmitschrift kommen runter.
"""
import asyncio
from typing import Awaitable, Callable

from google import genai
from google.genai import types

from audio_engine import AudioEngine
from config import AppConfig

StatusCallback = Callable[[str], None]
TranscriptCallback = Callable[[str, str], None]  # (wer, text)


def _is_normal_close(exc: BaseException) -> bool:
    """Erkennt ein normales Ende der Verbindung (Code 1000 = „alles ok, tschüss").
    Das ist kein Fehler und soll nicht als einer gemeldet werden."""
    if type(exc).__name__ == "ConnectionClosedOK":
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 1000


class GeminiLiveSession:
    def __init__(
        self,
        config: AppConfig,
        audio: AudioEngine,
        on_status: StatusCallback,
        on_transcript: TranscriptCallback | None = None,
    ):
        self.config = config
        self.audio = audio
        self.on_status = on_status
        self.on_transcript = on_transcript
        self.session = None  # aktive Verbindung (für spätere Text-Einspeisung)
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        client = genai.Client(api_key=self.config.api_key)
        connect_config = types.LiveConnectConfig(
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
        )

        self.on_status("Verbinde …")
        async with client.aio.live.connect(
            model=self.config.model, config=connect_config
        ) as session:
            self.session = session
            self.on_status("Verbunden")
            tasks = {
                asyncio.create_task(self._send_loop(session)),
                asyncio.create_task(self._receive_loop(session)),
                asyncio.create_task(self._stop.wait()),
            }
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
        self.session = None

    async def _send_loop(self, session) -> None:
        """Schickt Mikro-Audio Stück für Stück an Gemini."""
        try:
            while True:
                chunk = await self.audio.mic_to_gemini.get()
                await session.send_realtime_input(
                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                )
        except Exception as exc:
            if not _is_normal_close(exc):
                raise

    async def _receive_loop(self, session) -> None:
        """Empfängt Antworten: Audio abspielen, Text ins Fenster."""
        try:
            async for message in session.receive():
                self._handle_message(message)
        except Exception as exc:
            if not _is_normal_close(exc):
                raise

    def _handle_message(self, message) -> None:
        content = message.server_content
        if content is None:
            return

        if content.interrupted:
            # Nutzer hat die KI unterbrochen -> Rest der Antwort löschen
            self.audio.clear_playback()
            self.on_status("Verbunden (unterbrochen)")

        if content.model_turn:
            for part in content.model_turn.parts:
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

    async def send_text(self, text: str) -> None:
        """Schiebt eine unsichtbare Textnachricht in die laufende Sitzung.
        (Brauchen wir in Phase 4 für den Hintergrund-Prüfer.)"""
        if self.session is None:
            return
        await self.session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )
