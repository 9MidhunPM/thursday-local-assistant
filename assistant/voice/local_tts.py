"""Offline PCM synthesis through the optional, bundled eSpeak NG library.

The loader wrapper is MIT licensed; its bundled eSpeak NG 1.52.0 is GPLv3.
No audio device, executable, network service, or spoken-text command line is used.
Call ``synthesize_wav`` off the event loop and set ``cancelled`` when discarding
its result. eSpeak's process-global state is serialized across all callers.
"""

from __future__ import annotations

# These fixed user-facing failures deliberately exclude native/private diagnostics.
# ruff: noqa: TRY003, TRY301
import ctypes
import importlib.util
import io
import sys
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ctypes import CDLL

_LOCK = threading.Lock()
_ENGINE: _Engine | None = None
_CALLBACK = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p
)
MAX_TEXT_CHARS = 6000
MAX_AUDIO_SECONDS = 120
MAX_SYNTHESIS_SECONDS = 20


class LocalTTSError(RuntimeError):
    """Fixed, private-content-free offline speech failure."""


def available() -> bool:
    """Report whether the optional bundled loader is installed, without audio I/O."""
    return importlib.util.find_spec("espeakng_loader") is not None


@_CALLBACK
def _idle_callback(_samples, _count, _events):
    return 1


class _Engine:
    def __init__(self) -> None:
        try:
            from espeakng_loader import get_data_path, get_library_path  # noqa: PLC0415

            self.lib: CDLL = ctypes.CDLL(get_library_path())
            self.lib.espeak_Initialize.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
            ]
            self.lib.espeak_Initialize.restype = ctypes.c_int
            self.lib.espeak_SetSynthCallback.argtypes = [_CALLBACK]
            self.lib.espeak_SetSynthCallback.restype = None
            self.lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
            self.lib.espeak_SetVoiceByName.restype = ctypes.c_int
            self.lib.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            self.lib.espeak_SetParameter.restype = ctypes.c_int
            self.lib.espeak_Synth.argtypes = [
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_uint,
                ctypes.c_int,
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_uint),
                ctypes.c_void_p,
            ]
            self.lib.espeak_Synth.restype = ctypes.c_int
            # 2: synchronous PCM retrieval; 0x8000: return an error, never exit()
            # when the bundled data is missing. The API wants its parent directory.
            parent = str(Path(get_data_path()).parent).encode()
            self.rate = self.lib.espeak_Initialize(2, 0, parent, 0x8000)
            if not 8000 <= self.rate <= 48000:
                raise LocalTTSError("Offline speech could not initialize its voice data.")
            # Bundled 1.52 data names the British English voice "en", not "en-gb".
            if self.lib.espeak_SetVoiceByName(b"en") != 0:
                raise LocalTTSError("Offline English speech is unavailable.")
            if self.lib.espeak_SetParameter(1, 175, 0) != 0:  # Rate, words/minute.
                raise LocalTTSError("Offline speech could not configure its voice.")
            self.lib.espeak_SetSynthCallback(_idle_callback)
        except LocalTTSError:
            raise
        except Exception:
            raise LocalTTSError(
                "Offline speech is unavailable. Install Thursday's voice extra."
            ) from None


def _check_cancelled(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise InterruptedError("Offline speech was cancelled.")


def synthesize_wav(text: str, *, cancelled: threading.Event | None = None) -> bytes:
    """Produce bounded mono 16-bit WAV bytes; never play or persist speech here."""
    global _ENGINE
    if not text.strip() or "\0" in text or len(text) > MAX_TEXT_CHARS:
        raise LocalTTSError("Offline speech needs a nonempty, bounded text segment.")
    deadline = time.monotonic() + MAX_SYNTHESIS_SECONDS
    _check_cancelled(cancelled)
    while not _LOCK.acquire(timeout=0.05):
        _check_cancelled(cancelled)
        if time.monotonic() >= deadline:
            raise LocalTTSError("Offline speech is busy. Try again shortly.")
    try:
        _check_cancelled(cancelled)
        if _ENGINE is None:
            _ENGINE = _Engine()
        engine = _ENGINE
        chunks: list[bytes] = []
        total = 0
        limited = False

        @_CALLBACK
        def receive(samples, count, _events):
            nonlocal total, limited
            if cancelled is not None and cancelled.is_set():
                return 1
            if time.monotonic() >= deadline:
                limited = True
                return 1
            if samples and count > 0:
                total += count
                if total > engine.rate * MAX_AUDIO_SECONDS:
                    limited = True
                    return 1
                chunks.append(ctypes.string_at(samples, count * ctypes.sizeof(ctypes.c_short)))
            return 0

        engine.lib.espeak_SetSynthCallback(receive)
        try:
            # UTF-8 only: SSML and phoneme markup are deliberately not enabled.
            encoded = text.encode("utf-8") + b"\0"
            result = engine.lib.espeak_Synth(encoded, len(encoded), 0, 1, 0, 1, None, None)
        finally:
            # The synchronous API has completed; drop text/audio callback captures.
            engine.lib.espeak_SetSynthCallback(_idle_callback)
        _check_cancelled(cancelled)
        if limited:
            raise LocalTTSError("Offline speech exceeded its synthesis limit.")
        if result != 0 or not chunks:
            raise LocalTTSError("Offline speech did not produce audio.")
        pcm = b"".join(chunks)
        if not any(pcm):
            raise LocalTTSError("Offline speech did not produce audible samples.")
        if sys.byteorder != "little":
            samples = array("h", pcm)
            samples.byteswap()
            pcm = samples.tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(engine.rate)
            writer.writeframes(pcm)
        return output.getvalue()
    finally:
        _LOCK.release()
