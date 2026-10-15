"""Measured clip loudness; normalization never touches desktop volume controls."""

import array
import asyncio
import io
import json
import math
import re
import shutil
import stat
import subprocess
import wave

import pytest

from assistant.voice.audio import audio_levels
from assistant.voice.tts import EdgeTTS

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is optional")


def fixture_wav(amplitude):
    output = io.BytesIO()
    samples = array.array("h", (
        round(amplitude * 32767 * math.sin(2 * math.pi * 440 * index / 24000))
        for index in range(24000 * 4)
    ))
    with wave.open(output, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(24000)
        clip.writeframes(samples.tobytes())
    return output.getvalue()


def measured_loudness(path):
    probe = subprocess.run([
        "ffmpeg", "-hide_banner", "-i", str(path), "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-",
    ], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(re.findall(r"\{[^{}]+\}", probe.stderr)[-1])


@pytest.mark.parametrize("amplitude", [0.01, 0.7])
def test_quiet_and_loud_clips_reach_normal_speech_level(tmp_path, amplitude):
    path = tmp_path / "normalized.mp3"
    asyncio.run(EdgeTTS._encode_audio(fixture_wav(amplitude), path))
    measurement = measured_loudness(path)
    assert -16.7 <= float(measurement["input_i"]) <= -15.3
    assert float(measurement["input_tp"]) < -1.0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert max(audio_levels(path)["levels"]) > 0


def test_silent_clip_stays_silent(tmp_path):
    path = tmp_path / "silence.mp3"
    asyncio.run(EdgeTTS._encode_audio(fixture_wav(0), path))
    assert max(audio_levels(path)["levels"]) == 0


@pytest.mark.asyncio
async def test_normalization_cancellation_kills_encoder_and_writes_no_partial_file(
    monkeypatch, tmp_path,
):
    entered = asyncio.Event()
    events = []

    class Encoder:
        returncode = None

        async def communicate(self, _source):
            entered.set()
            await asyncio.Event().wait()

        def kill(self):
            events.append("kill")
            self.returncode = -9

        async def wait(self):
            events.append("reaped")

    async def create(*_args, **_kwargs):
        return Encoder()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    path = tmp_path / "cancelled.mp3"
    task = asyncio.create_task(EdgeTTS._encode_audio(b"private audio fixture", path))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["kill", "reaped"]
    assert not path.exists()
