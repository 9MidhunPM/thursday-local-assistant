from __future__ import annotations

import ctypes
import io
import threading
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assistant.voice import local_tts


class FakeLibrary:
    def __init__(self, action=None):
        self.callback = None
        self.action = action
        self.calls = []

    def espeak_SetSynthCallback(self, callback):
        self.callback = callback

    def espeak_Synth(self, *args):
        self.calls.append(args)
        if self.action:
            return self.action(self.callback)
        samples = (ctypes.c_short * 4)(0, 1000, -1000, 0)
        assert self.callback(samples, 4, None) == 0
        assert self.callback(None, 0, None) == 0
        return 0


def engine(monkeypatch, action=None):
    lib = FakeLibrary(action)
    monkeypatch.setattr(local_tts, "_ENGINE", SimpleNamespace(lib=lib, rate=22050))
    return lib


def test_pcm_is_encoded_as_wav_with_plain_utf8_only(monkeypatch):
    lib = engine(monkeypatch)
    result = local_tts.synthesize_wav("Hello, café <audio src='private' />")
    with wave.open(io.BytesIO(result)) as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 22050
        assert reader.getnframes() == 4
        assert any(reader.readframes(4))
    args = lib.calls[0]
    assert args[0].endswith(b"\0")
    assert b"caf\xc3\xa9" in args[0]
    assert args[5] == 1  # UTF-8; never SSML/phoneme markup.
    assert lib.callback is local_tts._idle_callback


@pytest.mark.parametrize("text", ["", "  ", "hidden\0text", "x" * 6001])
def test_invalid_text_does_not_enter_native_library(monkeypatch, text):
    lib = engine(monkeypatch)
    with pytest.raises(local_tts.LocalTTSError):
        local_tts.synthesize_wav(text)
    assert not lib.calls


def test_cancel_before_synthesis_does_not_enter_library(monkeypatch):
    lib = engine(monkeypatch)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        local_tts.synthesize_wav("Hello", cancelled=cancelled)
    assert not lib.calls


def test_cancel_inside_callback_aborts_and_discards_pcm(monkeypatch):
    cancelled = threading.Event()

    def action(callback):
        cancelled.set()
        samples = (ctypes.c_short * 2)(50, -50)
        assert callback(samples, 2, None) == 1
        return 0

    lib = engine(monkeypatch, action)
    with pytest.raises(InterruptedError):
        local_tts.synthesize_wav("Hello", cancelled=cancelled)
    assert lib.callback is local_tts._idle_callback
    assert not local_tts._LOCK.locked()


def test_waiting_for_library_is_cancellable(monkeypatch):
    engine(monkeypatch)
    cancelled = threading.Event()
    errors = []

    def work():
        try:
            local_tts.synthesize_wav("Hello", cancelled=cancelled)
        except InterruptedError as exc:
            errors.append(exc)

    local_tts._LOCK.acquire()
    worker = threading.Thread(target=work)
    try:
        worker.start()
        cancelled.set()
        worker.join(1)
        assert not worker.is_alive()
        assert len(errors) == 1
    finally:
        local_tts._LOCK.release()


def test_output_bound_aborts_before_copying_native_memory(monkeypatch):
    def action(callback):
        samples = (ctypes.c_short * 1)(1)
        assert callback(samples, 22050 * (local_tts.MAX_AUDIO_SECONDS + 1), None) == 1
        return 0

    engine(monkeypatch, action)
    with pytest.raises(local_tts.LocalTTSError, match="limit"):
        local_tts.synthesize_wav("Hello")


def test_time_bound_aborts_callback(monkeypatch):
    engine(monkeypatch, lambda callback: callback(None, 0, None) - 1)
    monkeypatch.setattr(local_tts.time, "monotonic", Mock(side_effect=[0, 30]))
    with pytest.raises(local_tts.LocalTTSError, match="limit"):
        local_tts.synthesize_wav("Hello")


@pytest.mark.parametrize("mode", ["empty", "silent", "error"])
def test_missing_or_silent_pcm_is_not_success(monkeypatch, mode):
    def action(callback):
        if mode == "silent":
            samples = (ctypes.c_short * 2)(0, 0)
            callback(samples, 2, None)
        return -1 if mode == "error" else 0

    engine(monkeypatch, action)
    with pytest.raises(local_tts.LocalTTSError):
        local_tts.synthesize_wav("Hello")


def test_real_bundled_library_produces_non_silent_wav(monkeypatch):
    pytest.importorskip("espeakng_loader")
    monkeypatch.setattr(local_tts, "_ENGINE", None)
    data = local_tts.synthesize_wav("Thursday is ready. This speech is generated locally.")
    with wave.open(io.BytesIO(data)) as reader:
        frames = reader.readframes(reader.getnframes())
        assert reader.getframerate() == 22050
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert len(frames) > 22050
        assert any(frames)


def test_real_callback_cancel_keeps_next_synthesis_usable(monkeypatch):
    pytest.importorskip("espeakng_loader")
    monkeypatch.setattr(local_tts, "_ENGINE", None)

    class CancelDuringCallbacks:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls >= 8

    cancelled = CancelDuringCallbacks()
    with pytest.raises(InterruptedError):
        local_tts.synthesize_wav("This is a longer speech segment. " * 10, cancelled=cancelled)
    assert cancelled.calls >= 8
    data = local_tts.synthesize_wav("Ready again.")
    with wave.open(io.BytesIO(data)) as reader:
        assert any(reader.readframes(reader.getnframes()))
