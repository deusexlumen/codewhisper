"""
Audio-Pipeline: Mikrofon rein, Lautsprecher raus.

Läuft komplett in eigenen Ton-Threads (sounddevice) und reicht die Daten
über Warteschlangen an das Hauptprogramm weiter. Dadurch blockiert weder
das Fenster noch das Netzwerk den Ton.
"""
import asyncio
import threading
from collections import deque

import numpy as np
import sounddevice as sd

# Mikro: 16 kHz (das erwartet Gemini als Eingabe)
INPUT_RATE = 16000
# Lautsprecher: 24 kHz (so liefert Gemini die Antworten)
OUTPUT_RATE = 24000
CHANNELS = 1
BLOCKSIZE = 1024
DTYPE = "int16"


class AudioEngine:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_mic_level=None,          # wird mit Werten 0.0–1.0 gerufen, wenn das Mikro Ton hört
        input_device: str | None = None,   # Geräte-Name (Teil davon reicht) oder None = Standard
        output_device: str | None = None,
    ):
        self.loop = loop
        self.on_mic_level = on_mic_level
        self.input_device = input_device
        self.output_device = output_device
        # Mikro-Daten, die an Gemini geschickt werden
        self.mic_to_gemini: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        # Antwort-Daten von Gemini, die abgespielt werden
        self.gemini_to_speaker: asyncio.Queue[bytes] = asyncio.Queue()

        self.muted = False
        self.running = False

        # Abspiel-Puffer (wird aus dem Ton-Thread gelesen, daher eigenes Lock)
        self._playback_chunks: deque[bytes] = deque(maxlen=500)
        self._current_chunk = bytearray()
        self._lock = threading.Lock()

        self._input_stream: sd.InputStream | None = None
        self._output_stream: sd.OutputStream | None = None
        self._mic_chunk_counter = 0

    # ---------- Lebenszyklus ----------

    def start(self) -> None:
        self.running = True
        self._input_stream = sd.InputStream(
            samplerate=INPUT_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._mic_callback,
        )
        self._output_stream = sd.OutputStream(
            samplerate=OUTPUT_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._speaker_callback,
        )
        self._input_stream.start()
        self._output_stream.start()

    def stop(self) -> None:
        self.running = False
        for stream in (self._input_stream, self._output_stream):
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
        self._input_stream = None
        self._output_stream = None

    # ---------- Mikrofon -> Warteschlange -> Gemini ----------

    def _mic_callback(self, indata, frames, time_info, status) -> None:
        """Wird vom Ton-Thread aufgerufen, sobald neue Mikro-Daten da sind."""
        if self.muted or not self.running:
            return
        try:
            self.loop.call_soon_threadsafe(self._enqueue_mic, bytes(indata))
        except RuntimeError:
            pass  # Programm wird gerade beendet

    def _enqueue_mic(self, data: bytes) -> None:
        try:
            self.mic_to_gemini.put_nowait(data)
        except asyncio.QueueFull:
            # Schlange voll -> ältestes Stück wegwerfen, damit wir live bleiben
            try:
                self.mic_to_gemini.get_nowait()
                self.mic_to_gemini.put_nowait(data)
            except asyncio.QueueEmpty:
                pass

    def clear_mic_queue(self) -> None:
        """Bei Stummschaltung Restbestände verwerfen."""
        while True:
            try:
                self.mic_to_gemini.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ---------- Gemini -> Warteschlange -> Lautsprecher ----------

    async def speaker_feeder(self) -> None:
        """Nimmt Antwort-Audio aus der Schlange und legt es in den Abspiel-Puffer."""
        while self.running:
            chunk = await self.gemini_to_speaker.get()
            with self._lock:
                self._playback_chunks.append(chunk)

    def clear_playback(self) -> None:
        """Wird aufgerufen, wenn die KI unterbrochen wurde:
        alles Geplante sofort wegwerfen, damit sie nicht „nachspricht"."""
        with self._lock:
            self._playback_chunks.clear()
            self._current_chunk.clear()

    def _speaker_callback(self, outdata, frames, time_info, status) -> None:
        """Wird vom Ton-Thread aufgerufen, wenn der Lautsprecher Daten braucht."""
        needed = frames * 2  # int16 = 2 Bytes pro Sample, mono
        out = bytearray()
        with self._lock:
            while len(out) < needed and (self._current_chunk or self._playback_chunks):
                if not self._current_chunk:
                    self._current_chunk = bytearray(self._playback_chunks.popleft())
                take = min(needed - len(out), len(self._current_chunk))
                out += self._current_chunk[:take]
                del self._current_chunk[:take]
        if len(out) < needed:
            out += b"\x00" * (needed - len(out))  # Stille auffüllen
        outdata[:] = np.frombuffer(bytes(out), dtype=DTYPE).reshape(-1, 1)


def list_audio_devices() -> str:
    """Gibt alle Ton-Geräte aus (zur Fehlersuche im Terminal)."""
    return str(sd.query_devices())
