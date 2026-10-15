"""Bounded private worker protocol, cancellation, and persistent model lifetime."""

import io
import struct
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from assistant.voice import neural_tts


@pytest.fixture
def worker(monkeypatch, tmp_path):
    script = tmp_path / "fixture_worker.py"
    script.write_text(
        "import sys,json,struct,time,io,wave\n"
        "for line in sys.stdin.buffer:\n"
        " data=json.loads(line)\n"
        " if data['text']=='wait': time.sleep(10)\n"
        " if data['text']=='exit': sys.exit(1)\n"
        " if data['text']=='oversize':\n"
        "  sys.stdout.buffer.write(struct.pack('!I', 999999999))\n"
        "  sys.stdout.buffer.flush();continue\n"
        " out=io.BytesIO()\n"
        " with wave.open(out,'wb') as target:\n"
        "  target.setnchannels(1);target.setsampwidth(2);target.setframerate(24000)\n"
        "  target.writeframes(b'\\x01\\x01'*2400)\n"
        " audio=out.getvalue()\n"
        " sys.stdout.buffer.write(struct.pack('!I',len(audio))+audio)\n"
        " sys.stdout.buffer.flush()\n"
    )
    instance = neural_tts.NeuralWorker(timeout=2)
    commands = []

    def command():
        args = [sys.executable, str(script)]
        commands.append(args)
        return args, neural_tts._worker_environment(Path(sys.executable))

    monkeypatch.setattr(instance, "_command", command)
    yield instance, commands
    instance.close()


def test_private_protocol_reuses_worker_and_returns_valid_audio(worker):
    instance, commands = worker
    audio = instance.synthesize("private fixture spoken text")
    first_pid = instance._process.pid
    assert instance.synthesize("second fixture") == audio
    assert instance._process.pid == first_pid
    assert len(commands) == 1
    assert "private fixture spoken text" not in repr(commands)
    with wave.open(io.BytesIO(audio), "rb") as source:
        assert source.getnframes() == 2400
        assert source.getframerate() == 24000


def test_worker_environment_excludes_credentials_and_desktop(monkeypatch):
    for name in ("OPENAI_API_KEY", "THURSDAY_API_TOKEN", "DISPLAY", "PULSE_SERVER", "PYTHONPATH"):
        monkeypatch.setenv(name, "private-fixture")
    environment = neural_tts._worker_environment(Path("/fixture/bin/python"))
    assert "private-fixture" not in repr(environment)
    assert environment["OMP_NUM_THREADS"] == "4"


def test_cancel_inflight_kills_worker_then_next_request_restarts(worker):
    instance, commands = worker
    cancelled = threading.Event()
    errors = []

    def run():
        try:
            instance.synthesize("wait", cancelled=cancelled)
        except InterruptedError as error:
            errors.append(str(error))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 1
    while instance._process is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert instance._process is not None
    process = instance._process
    cancelled.set()
    thread.join(1)
    assert not thread.is_alive()
    assert errors == ["Neural speech was cancelled."]
    assert process.poll() is not None
    assert instance.synthesize("replacement")
    assert len(commands) == 2


def test_cancel_waiting_request_does_not_kill_active_worker(worker):
    instance, _commands = worker
    cancel = threading.Event()
    cancel.set()
    instance.synthesize("initial")
    process = instance._process
    instance._lock.acquire()
    try:
        with pytest.raises(InterruptedError):
            instance.synthesize("cancelled", cancelled=cancel)
        assert process.poll() is None
    finally:
        instance._lock.release()


def test_close_cancels_active_worker_and_allows_new_runtime(worker):
    instance, commands = worker
    errors = []

    def run():
        try:
            instance.synthesize("wait")
        except InterruptedError as error:
            errors.append(str(error))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 1
    while instance._process is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert instance._process is not None
    start = time.monotonic()
    instance.close()
    thread.join(1)
    assert time.monotonic() - start < 1
    assert errors == ["Neural speech was cancelled."]
    assert instance._process is None
    assert instance.synthesize("restarted runtime")
    assert len(commands) == 2


@pytest.mark.parametrize("text", ["exit", "oversize"])
def test_invalid_worker_response_is_bounded_and_recoverable(worker, text):
    instance, _commands = worker
    with pytest.raises(RuntimeError, match="Natural voice"):
        instance.synthesize(text)
    assert instance._process is None
    assert instance.synthesize("recovery")


def test_timeout_kills_worker(worker):
    instance, _commands = worker
    instance._timeout = 0.1
    with pytest.raises(TimeoutError, match="time limit"):
        instance.synthesize("wait")
    assert instance._process is None


@pytest.mark.parametrize(("text", "voice", "speed"), [
    ("", "bm_george", 1), ("x" * 6001, "bm_george", 1),
    ("hello", "not-a-voice", 1), ("hello", "bm_george", float("nan")),
    ("hello", "bm_george", 1.3), ("hello", "bm_george", 0.7),
    ("hello", "bm_george", True),
])
def test_invalid_request_never_launches_worker(worker, text, voice, speed):
    instance, commands = worker
    with pytest.raises(ValueError, match=r"Neural speech|supported British"):
        instance.synthesize(text, voice=voice, speed=speed)
    assert commands == []


def test_invalid_audio_rejected():
    with pytest.raises(RuntimeError, match="invalid audio"):
        neural_tts._validate_wav(b"bad audio")
    header = b"RIFF" + struct.pack("<I", 36) + b"WAVE"
    with pytest.raises(RuntimeError, match="invalid audio"):
        neural_tts._validate_wav(header)
