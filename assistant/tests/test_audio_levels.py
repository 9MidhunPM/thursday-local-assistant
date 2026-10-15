"""Real decoded PCM measurements for the native speech visualizer."""

import math
import shutil
import struct
import subprocess
import wave
from types import SimpleNamespace

import pytest

from assistant.voice.audio import audio_levels


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="Audio decoder is optional")
def test_real_decoded_tone_and_silence_have_measured_levels(tmp_path):
    path = tmp_path / "tone-silence.wav"
    # Two windows silence, two windows tone at half amplitude, two windows silence.
    samples = [0] * 640
    samples += [round(16384 * math.sin(2 * math.pi * 500 * n / 8000)) for n in range(640)]
    samples += [0] * 640
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    measured = audio_levels(path)
    assert measured["interval_ms"] == 40
    assert measured["duration_ms"] == 240
    assert measured["levels"][:2] == [0, 0]
    assert measured["levels"][2:4] == pytest.approx([math.sqrt(0.125)] * 2, abs=0.0001)
    assert measured["levels"][4:] == [0, 0]


def test_missing_decoder_never_invents_audio_levels(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert audio_levels(tmp_path / "speech.mp3") is None


@pytest.mark.parametrize("failure", ["rejected", "empty", "timeout"])
def test_invalid_audio_or_decoder_failure_has_no_envelope(monkeypatch, tmp_path, failure):
    def decode(*_args, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffmpeg", 5)
        return SimpleNamespace(returncode=int(failure == "rejected"), stdout=b"")

    monkeypatch.setattr(shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(subprocess, "run", decode)
    assert audio_levels(tmp_path / "speech.mp3") is None
