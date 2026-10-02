"""
Discord-Voice-Bridge: reine Audio-Logik (kein Discord-, kein Netzwerk-Import).

Discord und Gemini sprechen zwei verschiedene Audio-„Dialekte":

    Discord  -> 48 kHz, Stereo, 16 Bit, feste 20-ms-Pakete (3840 Bytes)
    Gemini   <- 16 kHz, Mono,   16 Bit (Eingang)
    Gemini   -> 24 kHz, Mono,   16 Bit, beliebig große Stücke (Ausgang)

Dieses Modul übersetzt zwischen beiden und kümmert sich um die drei
Engpässe der Bridge: Takt (Ringpuffer, aus dem Discord alle 20 ms genau
ein Frame zieht), mehrere Sprecher (SpeakerGate) und Barge-In (lokales
Leeren des Puffers, bevor Gemini selbst „interrupted" meldet).

DiscordAudioAdapter hat dieselbe Schnittstelle wie AudioEngine
(mic_to_gemini, gemini_to_speaker, clear_playback()), damit
GeminiLiveSession unverändert für Discord wiederverwendet werden kann.
"""
import asyncio
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

DISCORD_RATE = 48000
DISCORD_CHANNELS = 2
# 20 ms bei 48 kHz = 960 Samples *pro Kanal*; Stereo, 2 Bytes je Sample
# -> 960 * 2 * 2 = 3840 Bytes (entspricht discord.opus.Encoder.FRAME_SIZE).
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * DISCORD_CHANNELS * 2
SILENCE_FRAME = bytes(FRAME_BYTES)

GEMINI_INPUT_RATE = 16000
GEMINI_OUTPUT_RATE = 24000

DISCORD_INSTRUCTION_ADDON = (
    "\n\nDu bist in einem Discord-Sprachkanal mit mehreren Personen. "
    "Vor dem Audio einer Person kommt ein Hinweis der Form "
    "„[Sprecherwechsel: Name]“. Merke dir, wer was gesagt hat, und sprich "
    "Personen mit Namen an, wenn es hilft. Lies diese Hinweise nie vor. "
    "Antworte nur, wenn du direkt angesprochen wirst oder eine Frage an dich "
    "geht -- Gespräche der anderen untereinander lässt du laufen."
)


def build_discord_instruction(base: str) -> str:
    """Hängt die Discord-Regeln (Sprecherwechsel-Hinweise) an den Basis-Prompt."""
    return base + DISCORD_INSTRUCTION_ADDON


def build_speaker_note(name: str) -> str:
    """Unsichtbarer Text-Hinweis, der vor dem Audio einer neuen Person an Gemini geht."""
    return f"[Sprecherwechsel: {name}]"


# ---------- Resampling ----------

def discord_to_gemini(pcm48_stereo: bytes) -> bytes:
    """48 kHz Stereo -> 16 kHz Mono.

    Erst Kanäle mitteln (Stereo -> Mono), dann je 3 Samples mitteln
    (48k / 3 = 16k). Das Mitteln wirkt als einfacher Tiefpass gegen
    Aliasing -- für Sprache reicht das, für Musik wäre es zu grob.
    Ein unvollständiger Rest (keine ganze 3er-Gruppe) wird verworfen;
    Discord-Frames (960 Samples) gehen immer glatt auf."""
    usable = len(pcm48_stereo) - len(pcm48_stereo) % 4  # ganze Stereo-Paare
    samples = np.frombuffer(pcm48_stereo[:usable], dtype=np.int16)
    mono = samples.reshape(-1, 2).astype(np.int32).mean(axis=1)
    mono = mono[: len(mono) - len(mono) % 3]
    down = mono.reshape(-1, 3).mean(axis=1)
    return np.clip(np.round(down), -32768, 32767).astype(np.int16).tobytes()


class GeminiToDiscordResampler:
    """24 kHz Mono -> 48 kHz Stereo, mit Gedächtnis über Stück-Grenzen.

    Lineare Interpolation um einen halben Sample versetzt: zwischen jedes
    Eingangs-Sample kommt der Mittelwert mit seinem Vorgänger. Der letzte
    Wert wird gemerkt, damit an den Grenzen zwischen Gemini-Stücken kein
    Knacken entsteht (Gemini-Stücke sind beliebig groß)."""

    def __init__(self) -> None:
        self._prev = 0

    def reset(self) -> None:
        self._prev = 0

    def process(self, pcm24_mono: bytes) -> bytes:
        x = np.frombuffer(pcm24_mono[: len(pcm24_mono) - len(pcm24_mono) % 2], dtype=np.int16)
        if len(x) == 0:
            return b""
        x = x.astype(np.int32)
        prev = np.concatenate(([self._prev], x[:-1]))
        out = np.empty(len(x) * 2, dtype=np.int32)
        out[0::2] = (prev + x) // 2
        out[1::2] = x
        self._prev = int(x[-1])
        stereo = np.repeat(out, 2)  # L = R
        return stereo.astype(np.int16).tobytes()


def rms_level(pcm: bytes) -> float:
    """Lautstärke 0.0-1.0 (RMS) eines int16-PCM-Stücks."""
    samples = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype=np.int16)
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)) / 32768.0)


# ---------- Takt: Ringpuffer für den 20-ms-Abruf ----------

class FrameRingBuffer:
    """Puffer zwischen Gemini (unregelmäßige Stücke) und Discord (alle 20 ms
    exakt FRAME_BYTES).

    Kein eigener Timer nötig: Discords AudioPlayer-Thread *zieht* alle 20 ms
    über read_frame() einen Frame. Weil der Abruf vom Discord-Takt gesteuert
    wird, kann keine Takt-Drift entstehen -- der Puffer füllt sich nur, wenn
    Gemini schneller als Echtzeit liefert (normal), und leert sich im
    Abspieltempo. Leerlauf -> Stille-Frame statt Abbruch.

    Thread-sicher: write()/barge_in() kommen aus dem Event-Loop bzw. dem
    Empfangs-Thread, read_frame() aus dem Discord-Player-Thread.

    Barge-In-Fenster: Nach barge_in() werden weitere Gemini-Daten verworfen,
    bis entweder release() kommt (Gemini meldet „interrupted") oder
    discard_seconds abgelaufen sind. Die Zeitgrenze verhindert, dass ein
    Fehlalarm (Husten, Hintergrundlärm, den Gemini nicht als Unterbrechung
    wertet) den Rest der Antwort dauerhaft verschluckt."""

    def __init__(
        self,
        max_seconds: float = 120.0,
        discard_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_bytes = int(max_seconds * DISCORD_RATE) * DISCORD_CHANNELS * 2
        self.discard_seconds = discard_seconds
        self._clock = clock
        self._chunks: deque[bytes] = deque()
        self._head = b""
        self._head_pos = 0
        self._size = 0
        self._discard_until = 0.0
        self._lock = threading.Lock()

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return self._size

    def is_playing(self) -> bool:
        return self.buffered_bytes > 0

    def is_discarding(self) -> bool:
        with self._lock:
            return self._clock() < self._discard_until

    def write(self, data: bytes) -> bool:
        """Hängt Audio an. Gibt False zurück, wenn es im Barge-In-Fenster
        verworfen wurde."""
        if not data:
            return True
        with self._lock:
            if self._clock() < self._discard_until:
                return False
            self._chunks.append(data)
            self._size += len(data)
            # Überlauf-Schutz: älteste Stücke weg (nur bei extrem langen
            # Antworten relevant; ganze Stücke, damit Stereo-Ausrichtung hält)
            while self._size > self.max_bytes and len(self._chunks) > 1:
                self._size -= len(self._chunks.popleft())
            return True

    def read_frame(self) -> bytes:
        """Genau FRAME_BYTES; fehlende Bytes werden mit Stille aufgefüllt."""
        out = bytearray()
        with self._lock:
            while len(out) < FRAME_BYTES:
                if self._head_pos >= len(self._head):
                    if not self._chunks:
                        break
                    self._head = self._chunks.popleft()
                    self._head_pos = 0
                take = min(FRAME_BYTES - len(out), len(self._head) - self._head_pos)
                out += self._head[self._head_pos : self._head_pos + take]
                self._head_pos += take
                self._size -= take
        if len(out) < FRAME_BYTES:
            out += bytes(FRAME_BYTES - len(out))
        return bytes(out)

    def clear(self) -> None:
        with self._lock:
            self._clear_locked()

    def barge_in(self) -> None:
        """Nutzer redet dazwischen: sofort leeren + Verwerf-Fenster öffnen."""
        with self._lock:
            self._clear_locked()
            self._discard_until = self._clock() + self.discard_seconds

    def release(self) -> None:
        """Gemini hat die Unterbrechung bestätigt: leeren, Fenster schließen
        -- was ab jetzt kommt, gehört zur neuen Antwort."""
        with self._lock:
            self._clear_locked()
            self._discard_until = 0.0

    def _clear_locked(self) -> None:
        self._chunks.clear()
        self._head = b""
        self._head_pos = 0
        self._size = 0


# ---------- Mehrere Sprecher ----------

class SpeakerGate:
    """Wer gerade redet, hat „das Wort".

    Gemini bekommt einen einzigen Mono-Strom. Würde man alle Discord-Nutzer
    einfach zusammenmischen, verschmelzen gleichzeitige Sprecher zu einer
    Phantom-Person. Stattdessen: Die erste Person behält das Wort, bis sie
    hold_seconds lang still ist; Audio anderer Personen in dieser Zeit wird
    verworfen. Bei einem echten Wechsel meldet accept() changed=True, damit
    vorher ein Sprecher-Hinweis an Gemini gehen kann.

    Bewusst ohne Lock: wird nur aus dem einen Empfangs-Thread aufgerufen."""

    def __init__(
        self,
        hold_seconds: float = 0.6,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.hold_seconds = hold_seconds
        self._clock = clock
        self.active_user: int | None = None
        self._last_seen = 0.0
        self._last_announced: int | None = None

    def accept(self, user_id: int) -> tuple[bool, bool]:
        """-> (Audio durchlassen?, Sprecher hat gewechselt?)"""
        now = self._clock()
        floor_free = (
            self.active_user is None or now - self._last_seen > self.hold_seconds
        )
        if user_id != self.active_user and not floor_free:
            return False, False
        self.active_user = user_id
        self._last_seen = now
        changed = user_id != self._last_announced
        self._last_announced = user_id
        return True, changed


# ---------- Adapter: Discord <-> GeminiLiveSession ----------

class DiscordAudioAdapter:
    """Tritt gegenüber GeminiLiveSession als „AudioEngine" auf.

    - mic_to_gemini: bekommt 16-kHz-Mono-Audio (bytes) und davor ggf. einen
      Sprecher-Hinweis (str) -- beides in *derselben* Warteschlange, damit
      der Hinweis garantiert vor dem Audio der neuen Person ankommt.
    - gemini_to_speaker: wird von speaker_feeder() geleert, hochgesampelt
      und in den Ringpuffer geschrieben.
    - clear_playback(): GeminiLiveSession ruft das bei „interrupted".
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        barge_in_threshold: float = 0.02,
        hold_seconds: float = 0.6,
        discard_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.loop = loop
        self.mic_to_gemini: asyncio.Queue[bytes | str] = asyncio.Queue(maxsize=200)
        self.gemini_to_speaker: asyncio.Queue[bytes] = asyncio.Queue()
        self.buffer = FrameRingBuffer(discard_seconds=discard_seconds, clock=clock)
        self.gate = SpeakerGate(hold_seconds=hold_seconds, clock=clock)
        self.resampler = GeminiToDiscordResampler()
        # 0 oder weniger schaltet lokales Barge-In ab (dann nur Geminis VAD)
        self.barge_in_threshold = barge_in_threshold

    # --- Eingang: aus dem Discord-Empfangs-Thread ---

    def feed_user_pcm(self, user_id: int, name: str, pcm48_stereo: bytes) -> None:
        """Ein 20-ms-Frame eines Discord-Nutzers. Läuft im Empfangs-Thread,
        nicht im Event-Loop -- Übergabe deshalb per call_soon_threadsafe."""
        accepted, changed = self.gate.accept(user_id)
        if not accepted:
            return
        if (
            self.barge_in_threshold > 0
            and self.buffer.is_playing()
            and rms_level(pcm48_stereo) >= self.barge_in_threshold
        ):
            # Lokal sofort still werden (< 20 ms), statt auf Geminis
            # „interrupted" (Netzwerk-Rundreise + Server-VAD) zu warten.
            self.buffer.barge_in()
            self.resampler.reset()
        pcm16 = discord_to_gemini(pcm48_stereo)
        if changed:
            self.loop.call_soon_threadsafe(self._put_nowait, build_speaker_note(name))
        self.loop.call_soon_threadsafe(self._put_nowait, pcm16)

    def _put_nowait(self, item: bytes | str) -> None:
        if self.mic_to_gemini.full():
            try:
                self.mic_to_gemini.get_nowait()  # Ältestes weg, damit es live bleibt
            except asyncio.QueueEmpty:
                pass
        self.mic_to_gemini.put_nowait(item)

    # --- Ausgang: Gemini -> Ringpuffer -> Discord ---

    async def speaker_feeder(self) -> None:
        while True:
            chunk = await self.gemini_to_speaker.get()
            self.buffer.write(self.resampler.process(chunk))

    def read_frame(self) -> bytes:
        """Wird vom Discord-Player-Thread alle 20 ms gerufen."""
        return self.buffer.read_frame()

    def clear_playback(self) -> None:
        # Auch schon geholte, noch nicht verarbeitete Gemini-Stücke verwerfen
        while not self.gemini_to_speaker.empty():
            try:
                self.gemini_to_speaker.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.buffer.release()
        self.resampler.reset()
