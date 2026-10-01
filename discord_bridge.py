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
"""
import asyncio

import discord
from discord.ext import voice_recv

from config import AppConfig
from discord_audio import DiscordAudioAdapter, build_discord_instruction
from gemini_session import GeminiLiveSession


class GeminiSink(voice_recv.AudioSink):
    """Empfängt dekodiertes PCM (48 kHz Stereo, 20 ms) pro Discord-Nutzer.
    write() läuft im Empfangs-Thread von voice_recv."""

    def __init__(self, adapter: DiscordAudioAdapter, bot_user_id: int):
        super().__init__()
        self.adapter = adapter
        self.bot_user_id = bot_user_id

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None or user.id == self.bot_user_id or not data.pcm:
            return
        self.adapter.feed_user_pcm(user.id, user.display_name, data.pcm)

    def cleanup(self) -> None:
        pass


class GeminiSource(discord.AudioSource):
    """Discords AudioPlayer-Thread ruft read() alle 20 ms. Liefert nie
    b"" (das würde die Wiedergabe beenden), sondern bei leerem Puffer
    Stille."""

    def __init__(self, adapter: DiscordAudioAdapter):
        self.adapter = adapter

    def read(self) -> bytes:
        return self.adapter.read_frame()

    def is_opus(self) -> bool:
        return False


class BridgeClient(discord.Client):
    def __init__(self, config: AppConfig):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.members = False
        super().__init__(intents=intents)
        self.config = config
        self._started = False

    async def on_ready(self) -> None:
        # on_ready kann bei Reconnects mehrfach kommen -- nur einmal starten
        if self._started:
            return
        self._started = True
        print(f"Discord: angemeldet als {self.user}")
        try:
            await self._run_bridge()
        finally:
            await self.close()

    async def _run_bridge(self) -> None:
        channel = self.get_channel(self.config.discord_voice_channel_id)
        if channel is None:
            channel = await self.fetch_channel(self.config.discord_voice_channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            print("discord_voice_channel_id ist kein Sprachkanal.")
            return

        adapter = DiscordAudioAdapter(asyncio.get_running_loop())
        vc: voice_recv.VoiceRecvClient = await channel.connect(
            cls=voice_recv.VoiceRecvClient
        )
        vc.listen(GeminiSink(adapter, self.user.id))
        vc.play(GeminiSource(adapter))

        session = GeminiLiveSession(
            config=self.config,
            audio=adapter,
            on_status=lambda s: print(f"[Status] {s}"),
            on_transcript=lambda who, text: print(f"[{who}] {text}"),
            enable_tools=False,
        )
        feeder_task = asyncio.create_task(adapter.speaker_feeder())
        try:
            await session.run()
        finally:
            feeder_task.cancel()
            await asyncio.gather(feeder_task, return_exceptions=True)
            vc.stop_listening()
            vc.stop()
            await vc.disconnect()


def main() -> None:
    config = AppConfig.load()
    if not config.discord_token or not config.discord_voice_channel_id:
        raise SystemExit(
            "Für die Discord-Bridge fehlen „discord_token“ und/oder "
            "„discord_voice_channel_id“ in config.json (siehe README.md)."
        )
    config.system_instruction = build_discord_instruction(config.system_instruction)
    BridgeClient(config).run(config.discord_token)


if __name__ == "__main__":
    main()
