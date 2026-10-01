import asyncio

import numpy as np
import pytest

from discord_audio import (
    FRAME_BYTES,
    SILENCE_FRAME,
    DiscordAudioAdapter,
    FrameRingBuffer,
    GeminiToDiscordResampler,
    SpeakerGate,
    build_discord_instruction,
    build_speaker_note,
    discord_to_gemini,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _stereo_frame(value: int) -> bytes:
    return np.full(960 * 2, value, dtype=np.int16).tobytes()


def test_frame_bytes_is_3840():
    # 960 Samples pro Kanal * 2 Kanäle * 2 Bytes -- nicht 1920
    assert FRAME_BYTES == 3840


def test_discord_to_gemini_sizes_and_values():
    out = discord_to_gemini(_stereo_frame(1000))
    # 20 ms bei 16 kHz Mono = 320 Samples = 640 Bytes
    assert len(out) == 640
    assert set(np.frombuffer(out, dtype=np.int16)) == {1000}


def test_discord_to_gemini_mixes_channels():
    stereo = np.tile(np.array([1000, -1000], dtype=np.int16), 960).tobytes()
    out = np.frombuffer(discord_to_gemini(stereo), dtype=np.int16)
    assert (out == 0).all()


def test_discord_to_gemini_tolerates_odd_length():
    assert discord_to_gemini(b"\x01\x02\x03") == b""


def test_resampler_doubles_rate_and_goes_stereo():
    r = GeminiToDiscordResampler()
    mono = np.array([100, 200, 300], dtype=np.int16).tobytes()
    out = np.frombuffer(r.process(mono), dtype=np.int16).reshape(-1, 2)
    assert (out[:, 0] == out[:, 1]).all()
    assert list(out[:, 0]) == [50, 100, 150, 200, 250, 300]


def test_resampler_remembers_previous_chunk():
    r = GeminiToDiscordResampler()
    r.process(np.array([1000], dtype=np.int16).tobytes())
    out = np.frombuffer(r.process(np.array([2000], dtype=np.int16).tobytes()), dtype=np.int16)
    # Erstes Sample interpoliert zwischen 1000 (vorheriges Stück) und 2000
    assert out[0] == 1500


def test_ring_buffer_underflow_returns_silence():
    assert FrameRingBuffer().read_frame() == SILENCE_FRAME


def test_ring_buffer_reassembles_irregular_chunks_into_frames():
    buf = FrameRingBuffer()
    data = bytes(range(256)) * 30  # 7680 Bytes = genau 2 Frames
    buf.write(data[:1000])
    buf.write(data[1000:5000])
    buf.write(data[5000:])
    assert buf.read_frame() + buf.read_frame() == data
    assert buf.buffered_bytes == 0
    assert buf.read_frame() == SILENCE_FRAME


def test_ring_buffer_pads_partial_frame():
    buf = FrameRingBuffer()
    buf.write(b"\x01" * 100)
    frame = buf.read_frame()
    assert len(frame) == FRAME_BYTES
    assert frame[:100] == b"\x01" * 100 and frame[100:] == bytes(FRAME_BYTES - 100)


def test_barge_in_discards_until_release():
    clock = FakeClock()
    buf = FrameRingBuffer(discard_seconds=1.0, clock=clock)
    buf.write(b"\x01" * 8000)
    buf.barge_in()
    assert buf.buffered_bytes == 0
    assert buf.write(b"\x02" * 100) is False
    buf.release()
    assert buf.write(b"\x03" * 100) is True


def test_barge_in_window_expires_on_false_alarm():
    clock = FakeClock()
    buf = FrameRingBuffer(discard_seconds=1.0, clock=clock)
    buf.barge_in()
    clock.now = 1.5
    assert buf.write(b"\x01" * 100) is True


def test_speaker_gate_holds_floor_then_switches():
    clock = FakeClock()
    gate = SpeakerGate(hold_seconds=0.5, clock=clock)
    assert gate.accept(1) == (True, True)
    clock.now = 0.1
    assert gate.accept(2) == (False, False)  # 1 hat noch das Wort
    assert gate.accept(1) == (True, False)
    clock.now = 1.0
    assert gate.accept(2) == (True, True)  # 1 war lange genug still


def test_speaker_gate_same_user_after_pause_is_not_a_change():
    clock = FakeClock()
    gate = SpeakerGate(hold_seconds=0.5, clock=clock)
    gate.accept(1)
    clock.now = 10.0
    assert gate.accept(1) == (True, False)


def test_instruction_and_note():
    assert build_discord_instruction("Basis").startswith("Basis")
    assert build_speaker_note("Anna") == "[Sprecherwechsel: Anna]"


async def _drain(queue):
    await asyncio.sleep(0)  # call_soon_threadsafe-Callbacks laufen lassen
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


@pytest.mark.asyncio
async def test_adapter_sends_note_before_audio_on_speaker_change():
    clock = FakeClock()
    adapter = DiscordAudioAdapter(asyncio.get_running_loop(), clock=clock)
    adapter.feed_user_pcm(1, "Anna", _stereo_frame(10))
    adapter.feed_user_pcm(1, "Anna", _stereo_frame(10))
    items = await _drain(adapter.mic_to_gemini)
    assert items[0] == "[Sprecherwechsel: Anna]"
    assert [type(i) for i in items[1:]] == [bytes, bytes]


@pytest.mark.asyncio
async def test_adapter_local_barge_in_clears_playback():
    clock = FakeClock()
    adapter = DiscordAudioAdapter(asyncio.get_running_loop(), clock=clock)
    adapter.buffer.write(b"\x01" * 40000)
    adapter.feed_user_pcm(1, "Anna", _stereo_frame(8000))  # laut genug
    assert adapter.buffer.buffered_bytes == 0
    assert adapter.buffer.is_discarding()


@pytest.mark.asyncio
async def test_adapter_quiet_audio_does_not_barge_in():
    clock = FakeClock()
    adapter = DiscordAudioAdapter(asyncio.get_running_loop(), clock=clock)
    adapter.buffer.write(b"\x01" * 40000)
    adapter.feed_user_pcm(1, "Anna", _stereo_frame(5))
    assert adapter.buffer.buffered_bytes == 40000


@pytest.mark.asyncio
async def test_adapter_clear_playback_drops_pending_and_reopens():
    adapter = DiscordAudioAdapter(asyncio.get_running_loop())
    adapter.gemini_to_speaker.put_nowait(b"\x01\x00" * 100)
    adapter.buffer.barge_in()
    adapter.clear_playback()
    assert adapter.gemini_to_speaker.empty()
    assert not adapter.buffer.is_discarding()
