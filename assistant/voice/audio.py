"""Measure playback envelopes from decoded audio without using the microphone."""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SAMPLE_RATE = 8000
INTERVAL_MS = 40
MAX_SECONDS = 180


def audio_levels(path: Path) -> dict[str, object] | None:
    """Return normalized PCM RMS windows, or no envelope if decoding is unavailable.

    A fixed decoder duration bounds memory, and no process argument contains speech.
    Silence remains exactly zero; any visual scaling belongs to the renderer.
    """
    decoder = shutil.which("ffmpeg")
    if not decoder:
        return None
    try:
        result = subprocess.run(
            [decoder, "-nostdin", "-v", "error", "-i", str(path), "-t", str(MAX_SECONDS),
             "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode or not result.stdout or len(result.stdout) % 2:
        return None
    samples = array("h", result.stdout)
    if sys.byteorder != "little":
        samples.byteswap()
    window = SAMPLE_RATE * INTERVAL_MS // 1000
    levels = []
    for offset in range(0, len(samples), window):
        chunk = samples[offset:offset + window]
        rms = math.sqrt(sum(value * value for value in chunk) / len(chunk)) / 32768
        levels.append(round(rms, 5))
    return {
        "interval_ms": INTERVAL_MS,
        "duration_ms": round(len(samples) * 1000 / SAMPLE_RATE),
        "levels": levels,
    }
