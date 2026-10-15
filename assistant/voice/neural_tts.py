"""Private, cancellable Kokoro synthesis in a persistent Python 3.13 worker.

The main application can use newer Python versions without importing the neural
runtime. Requests travel through stdin and audio through stdout; neither contains
log messages. Dependencies and verified model files are installed explicitly by
``python -m assistant.voice.neural_setup``.
"""

from __future__ import annotations

# Deliberately fixed errors: provider exceptions can contain the spoken text.
# ruff: noqa: TRY003
import atexit
import contextlib
import io
import json
import math
import os
import selectors
import struct
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

MAX_TEXT_CHARS = 6000
MAX_REQUEST_BYTES = 65536
MAX_AUDIO_BYTES = 6_000_000
MAX_AUDIO_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 45.0
VOICES = frozenset({"bm_george", "bm_lewis"})


def _validate(text: str, voice: str, speed: float) -> None:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
        raise ValueError("Neural speech text is empty or too long.")
    if voice not in VOICES:
        raise ValueError("Choose a supported British neural voice.")
    if (isinstance(speed, bool) or not isinstance(speed, (int, float))
            or not math.isfinite(speed) or not 0.8 <= speed <= 1.2):
        raise ValueError("Neural speech speed must be between 0.8 and 1.2.")


def _worker_environment(python: Path) -> dict[str, str]:
    """No API keys, desktop session credentials, or provider configuration."""
    environment = {
        "PATH": f"{python.parent}:/usr/bin:/bin",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4",
        "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in ("HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    return environment


class NeuralWorker:
    """Serialize requests; kill a cancelled worker and recreate it next time."""

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._timeout = timeout
        self._closing = threading.Event()

    def _command(self) -> tuple[list[str], dict[str, str]]:
        from assistant.voice.neural_setup import (  # noqa: PLC0415
            models_dir,
            python_path,
            verify_models,
        )

        python, models = python_path(), models_dir()
        if not python.is_file() or not verify_models(models):
            raise RuntimeError(
                "Natural voice is not installed. Run python -m assistant.voice.neural_setup."
            )
        command = [
            str(python), str(Path(__file__).resolve()), "--worker",
            str(models / "kokoro-v1.0.fp16.onnx"), str(models / "voices-v1.0.bin"),
        ]
        return command, _worker_environment(python)

    def _start(self) -> subprocess.Popen:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._discard()
        command, environment = self._command()
        self._process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, bufsize=0,
        )
        os.set_blocking(self._process.stdin.fileno(), False)
        os.set_blocking(self._process.stdout.fileno(), False)
        return self._process

    def _discard(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            with contextlib.suppress(OSError):
                process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()

    def close(self) -> None:
        self._closing.set()
        try:
            with self._lock:
                self._discard()
        finally:
            self._closing.clear()

    def _check(self, cancelled: threading.Event, deadline: float) -> None:
        if cancelled.is_set() or self._closing.is_set():
            raise InterruptedError("Neural speech was cancelled.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Neural speech exceeded its time limit.")

    def synthesize(
        self, text: str, *, cancelled: threading.Event | None = None,
        voice: str = "bm_george", speed: float = 1.0,
    ) -> bytes:
        _validate(text, voice, speed)
        request = json.dumps(
            {"text": text, "voice": voice, "speed": speed}, ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        if len(request) > MAX_REQUEST_BYTES:
            raise ValueError("Neural speech request is too large.")
        cancel = cancelled if cancelled is not None else threading.Event()
        deadline = time.monotonic() + self._timeout
        while not self._lock.acquire(timeout=0.05):
            self._check(cancel, deadline)
        try:
            self._check(cancel, deadline)
            process = self._start()
            self._write(process.stdin, request, cancel, deadline)
            size = struct.unpack("!I", self._read(process.stdout, 4, cancel, deadline))[0]
            _validate_size(size)
            audio = self._read(process.stdout, size, cancel, deadline)
            _validate_wav(audio)
        except BaseException:
            self._discard()
            raise
        else:
            return audio
        finally:
            self._lock.release()

    def _write(self, stream, data: bytes, cancelled: threading.Event, deadline: float) -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_WRITE)
            offset = 0
            while offset < len(data):
                self._check(cancelled, deadline)
                if not selector.select(0.05):
                    continue
                offset += os.write(stream.fileno(), data[offset:])

    def _read(self, stream, size: int, cancelled: threading.Event, deadline: float) -> bytes:
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            while len(data) < size:
                self._check(cancelled, deadline)
                if not selector.select(0.05):
                    continue
                chunk = os.read(stream.fileno(), min(65536, size - len(data)))
                if not chunk:
                    raise RuntimeError("Natural voice worker stopped unexpectedly.")
                data.extend(chunk)
        return bytes(data)


def _validate_size(size: int) -> None:
    if not 44 <= size <= MAX_AUDIO_BYTES:
        raise RuntimeError("Natural voice could not synthesize this response.")


def _validate_wav(audio: bytes) -> None:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            valid = (source.getnchannels() == 1 and source.getsampwidth() == 2
                     and source.getframerate() == 24000
                     and 0 < source.getnframes() <= MAX_AUDIO_SECONDS * 24000
                     and len(source.readframes(source.getnframes())) == source.getnframes() * 2)
    except (wave.Error, EOFError, ValueError):
        raise RuntimeError("Natural voice returned invalid audio.") from None
    if not valid:
        raise RuntimeError("Natural voice returned invalid audio.")


_worker = NeuralWorker()
atexit.register(_worker.close)


def close_worker() -> None:
    _worker.close()


def synthesize_wav(
    text: str, *, cancelled: threading.Event | None = None,
    voice: str = "bm_george", speed: float = 1.0,
) -> bytes:
    return _worker.synthesize(text, cancelled=cancelled, voice=voice, speed=speed)


def _render(engine, np, text: str, voice: str, speed: float) -> bytes:
    _validate(text, voice, speed)
    samples, sample_rate = engine.create(text, voice=voice, speed=speed, lang="en-gb")
    if (sample_rate != 24000 or samples.ndim != 1
            or not 0 < len(samples) <= MAX_AUDIO_SECONDS * 24000
            or not np.isfinite(samples).all() or not np.any(samples)):
        raise ValueError("Invalid neural audio.")
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(24000)
        target.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


def _run_worker(model_path: str, voices_path: str) -> None:
    """Standalone entry point: all optional imports stay inside Python 3.13."""
    response_stream = sys.stdout.buffer
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
        from kokoro_onnx import Kokoro  # noqa: PLC0415

        ort.disable_telemetry_events()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.log_severity_level = 4
        session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"],
        )
        engine = Kokoro.from_session(session, voices_path)
        while request := sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1):
            if len(request) > MAX_REQUEST_BYTES or not request.endswith(b"\n"):
                return
            try:
                payload = json.loads(request)
                text, voice, speed = payload["text"], payload["voice"], payload["speed"]
                audio = _render(engine, np, text, voice, speed)
            except Exception:
                audio = b""
            response_stream.write(struct.pack("!I", len(audio)))
            response_stream.write(audio)
            response_stream.flush()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        _run_worker(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("Install natural speech with: python -m assistant.voice.neural_setup")
