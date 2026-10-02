"""
Discord-Voice-Bridge: Gemini Live als Teilnehmer in einem Discord-Sprachkanal.

Start:  python discord_bridge.py
Braucht in config.json zusätzlich "discord_token" (oder Umgebungsvariable
DISCORD_TOKEN) und "discord_voice_channel_id".

Wiederverwendet GeminiLiveSession unverändert -- statt AudioEngine
(Mikro/Lautsprecher) bekommt sie einen DiscordAudioAdapter mit derselben
Schnittstelle. Die eigentliche Audio-Logik steckt in discord_audio.py;
hier ist nur die Discord-Verdrahtung.

Hinweis: discord.py kann selbst kein Audio *empfangen*, dafür ist
discord-ext-voice-recv nötig (bringt auch den Jitter-Puffer für die
eingehenden RTP-Pakete mit, pro SSRC).

Robustheit: Gemini-Seite verbindet sich selbst neu (GeminiLiveSession).
Für die Discord-Seite prüft ein Wächter alle paar Sekunden, ob der Bot
noch im Kanal ist, noch zuhört und noch abspielt -- und repariert, was
fehlt (Kick, Netzwerk-Abbruch, abgestürzter Empfangs-/Abspiel-Thread).
"""
import asyncio
import logging

import discord
from discord.ext import voice_recv

from config import AppConfig
from discord_audio import SILENCE_FRAME, DiscordAudioAdapter, build_discord_instruction
from gemini_session import GeminiLiveSession
from reconnect import ReconnectPolicy

log = logging.getLogger("discord_bridge")

WATCHDOG_INTERVAL = 5.0
STATS_INTERVAL = 60.0


class GeminiSink(voice_recv.AudioSink):
    """Empfängt dekodiertes PCM (48 kHz Stereo, 20 ms) pro Discord-Nutzer.
    write() läuft im Empfangs-Thread von voice_recv -- eine Exception hier
    würde diesen Thread beenden, deshalb wird alles abgefangen."""

    def __init__(self, adapter: DiscordAudioAdapter, bot_user_id: int):
        super().__init__()
        self.adapter = adapter
        self.bot_user_id = bot_user_id

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData) -> None:
        try:
            if user is None or user.id == self.bot_user_id or user.bot or not data.pcm:
                return
            self.adapter.feed_user_pcm(user.id, user.display_name, data.pcm)
        except Exception:
            log.exception("Fehler beim Verarbeiten von Discord-Audio")

    def cleanup(self) -> None:
        pass


class GeminiSource(discord.AudioSource):
    """Discords AudioPlayer-Thread ruft read() alle 20 ms. Liefert nie
    b"" (das würde die Wiedergabe beenden), sondern bei leerem Puffer
    Stille -- auch im Fehlerfall."""

    def __init__(self, adapter: DiscordAudioAdapter):
        self.adapter = adapter

    def read(self) -> bytes:
        try:
            return self.adapter.read_frame()
        except Exception:
            return SILENCE_FRAME

    def is_opus(self) -> bool:
        return False


class BridgeClient(discord.Client):
    def __init__(self, config: AppConfig):
        intents = discord.Intents.default()
        intents.voice_states = True
        super().__init__(intents=intents)
        self.config = config
        self._started = False
        self.vc: voice_recv.VoiceRecvClient | None = None
        self.adapter: DiscordAudioAdapter | None = None
        self.exit_code = 0

    async def on_ready(self) -> None:
        # on_ready kommt nach jedem Gateway-Resume erneut -- nur einmal starten
        if self._started:
            return
        self._started = True
        log.info("Discord: angemeldet als %s", self.user)
        try:
            await self._run_bridge()
        except Exception:
            log.exception("Bridge beendet wegen eines nicht behebbaren Fehlers")
            self.exit_code = 1
        finally:
            await self._leave_voice()
            await self.close()

    async def _resolve_channel(self):
        channel = self.get_channel(self.config.discord_voice_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.config.discord_voice_channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            raise ValueError("discord_voice_channel_id ist kein Sprachkanal.")
        return channel

    async def _run_bridge(self) -> None:
        channel = await self._resolve_channel()
        self.adapter = DiscordAudioAdapter(asyncio.get_running_loop())

        session = GeminiLiveSession(
            config=self.config,
            audio=self.adapter,
            on_status=lambda s: log.info("[Gemini] %s", s),
            on_transcript=lambda who, text: log.info("[%s] %s", who, text),
            enable_tools=False,
        )
        feeder = asyncio.create_task(self.adapter.speaker_feeder(), name="feeder")
        gemini = asyncio.create_task(session.run(), name="gemini")
        watchdog = asyncio.create_task(self._voice_watchdog(channel), name="watchdog")
        tasks = {feeder, gemini, watchdog}
        try:
            # Normalerweise läuft alles bis Strg+C. Endet einer der Tasks
            # (z. B. Gemini mit ungültigem API-Key), ist die Bridge kaputt
            # -> alles geordnet herunterfahren statt halb weiterzulaufen.
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    raise task.exception()
        finally:
            await session.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # ---------- Sprachkanal-Wächter ----------

    async def _voice_watchdog(self, channel) -> None:
        policy = ReconnectPolicy(max_attempts=10**9)  # Discord-Seite nie aufgeben
        loop = asyncio.get_running_loop()
        connected_since = None
        last_stats = loop.time()
        missing_checks = 0
        while True:
            vc = self.vc
            missing_checks = missing_checks + 1 if vc is None or not vc.is_connected() else 0
            # Eine bestehende Verbindung erst nach zwei Fehl-Prüfungen
            # (~10 s) ersetzen: discord.py verbindet bei kurzen Aussetzern
            # selbst neu, da soll der Wächter nicht dazwischenfunken.
            if vc is None or missing_checks >= 2:
                if connected_since is not None:
                    policy.connection_ended(loop.time() - connected_since)
                    connected_since = None
                try:
                    await self._join(channel)
                    connected_since = loop.time()
                    missing_checks = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    delay = policy.next_delay()
                    log.warning(
                        "Sprachkanal-Verbindung fehlgeschlagen (%s), neuer Versuch in %.1f s",
                        exc, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
            elif missing_checks == 0:
                self._ensure_streams(vc)

            now = loop.time()
            if now - last_stats >= STATS_INTERVAL:
                last_stats = now
                a = self.adapter
                log.info(
                    "Statistik: %d Discord-Frames empfangen (%d verworfen, Sprecher-Gate), "
                    "%.1f s KI-Audio gepuffert",
                    a.frames_in, a.frames_dropped, a.buffer.buffered_bytes / (48000 * 4),
                )
            await asyncio.sleep(WATCHDOG_INTERVAL)

    async def _join(self, channel) -> None:
        # Halbtote alte Verbindung (z. B. nach Kick) erst sauber wegräumen
        stale = channel.guild.voice_client
        if stale is not None:
            await self._disconnect(stale)
        self.vc = None
        log.info("Trete Sprachkanal %s bei …", channel.name)
        self.vc = await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=20.0)
        self.adapter.buffer.clear()
        self._ensure_streams(self.vc)
        log.info("Im Sprachkanal %s", channel.name)

    def _ensure_streams(self, vc: voice_recv.VoiceRecvClient) -> None:
        """Startet Empfang/Wiedergabe neu, falls deren Thread beendet ist."""
        if not vc.is_listening():
            try:
                vc.listen(GeminiSink(self.adapter, self.user.id), after=_log_after("Empfang"))
            except discord.ClientException as exc:
                log.warning("Empfang nicht startbar: %s", exc)
        if not (vc.is_playing() or vc.is_paused()):
            try:
                vc.play(GeminiSource(self.adapter), after=_log_after("Wiedergabe"))
            except discord.ClientException as exc:
                log.warning("Wiedergabe nicht startbar: %s", exc)

    async def _leave_voice(self) -> None:
        if self.vc is not None:
            await self._disconnect(self.vc)
            self.vc = None

    @staticmethod
    async def _disconnect(vc) -> None:
        try:
            if hasattr(vc, "stop_listening") and vc.is_listening():
                vc.stop_listening()
            vc.stop()
            await asyncio.wait_for(vc.disconnect(force=True), timeout=5.0)
        except Exception as exc:
            log.debug("Trennen vom Sprachkanal: %s", exc)


def _log_after(what: str):
    """after-Callback für listen()/play(): läuft in deren Thread, darf also
    nichts am Event-Loop anfassen -- nur loggen. Den Neustart erledigt der
    Wächter beim nächsten Durchlauf."""

    def after(error: Exception | None) -> None:
        if error is not None:
            log.warning("%s beendet mit Fehler: %r", what, error)
        else:
            log.info("%s beendet", what)

    return after


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    config = AppConfig.load()
    if not config.discord_token or not config.discord_voice_channel_id:
        raise SystemExit(
            "Für die Discord-Bridge fehlen „discord_token“ und/oder "
            "„discord_voice_channel_id“ in config.json (siehe README.md)."
        )
    config.system_instruction = build_discord_instruction(config.system_instruction)
    client = BridgeClient(config)
    # log_handler=None: unser basicConfig oben gilt, discord.py richtet kein eigenes ein
    client.run(config.discord_token, log_handler=None)
    raise SystemExit(client.exit_code)


if __name__ == "__main__":
    main()
