"""One session-owned microphone workflow shared by native controls and hotkeys."""

from __future__ import annotations

import contextlib
import io
import logging
import threading
import uuid
import wave
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
CHUNK = 1024
MAX_HOLD_SECONDS = 55


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def wav_bytes(self) -> bytes: ...


class MicrophoneRecorder:
    """Capture bounded mono PCM in memory; never write recorded speech to disk."""

    def __init__(self) -> None:
        import pyaudio  # noqa: PLC0415 - optional until microphone input is requested

        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._pa = pyaudio.PyAudio()
        self._stream = None
        self._closed = False
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK,
                stream_callback=self._on_chunk,
                start=False,
            )
        except Exception:
            self._pa.terminate()
            raise

    def _on_chunk(self, audio, _frame_count, _time_info, _status):
        with self._lock:
            # Retain the beginning of a long utterance, never silently replace
            # its instructions with only the tail. The controller auto-stops.
            if len(self._frames) * CHUNK < MAX_HOLD_SECONDS * SAMPLE_RATE:
                self._frames.append(bytes(audio))
        return None, 0  # paContinue

    def start(self) -> None:
        if self._stream is not None:
            self._stream.start_stream()

    def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                finally:
                    self._stream.close()
        finally:
            self._pa.terminate()

    def wav_bytes(self) -> bytes:
        with self._lock:
            audio = b"".join(self._frames)
            self._frames.clear()
        if len(audio) < SAMPLE_RATE * 2 // 4:
            return b""
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(audio)
        return output.getvalue()


@dataclass
class _Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "listening"
    transcript: str = ""
    error: str = ""
    task_id: str | None = None
    conversation_id: Any = None
    recorder: Recorder | None = None
    timer: threading.Timer | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict:
        result = {
            "id": self.id,
            "session_id": self.id,
            "state": self.state,
            "transcript": self.transcript,
            "error": self.error,
        }
        if self.task_id is not None:
            result["task_id"] = self.task_id
        if self.conversation_id is not None:
            result["conversation_id"] = self.conversation_id
        return result


class VoiceController:
    def __init__(
        self,
        *,
        transcribe: Callable[[bytes], str | None],
        submit: Callable[[str], dict],
        interrupt: Callable[[], None],
        publish: Callable[[dict], None],
        recorder_factory: Callable[[], Recorder] = MicrophoneRecorder,
        wait_until_ready: Callable[[threading.Event], bool] | None = None,
    ) -> None:
        self._transcribe = transcribe
        self._submit = submit
        self._interrupt = interrupt
        self._publish = publish
        self._recorder_factory = recorder_factory
        self._wait_until_ready = wait_until_ready or (lambda cancelled: not cancelled.is_set())
        self._lock = threading.RLock()
        self._session: _Session | None = None
        self._workers: set[threading.Thread] = set()
        self._closed = False

    def snapshot(self) -> dict:
        with self._lock:
            if self._session is None:
                return {
                    "id": None, "session_id": None, "state": "idle", "transcript": "", "error": ""
                }
            return self._session.snapshot()

    def _emit(self) -> dict:
        state = self.snapshot()
        try:
            self._publish(dict(state))
        except Exception:
            log.warning("Voice state delivery failed")
        return state

    @staticmethod
    def _release(session: _Session) -> None:
        if session.timer is not None:
            session.timer.cancel()
            session.timer = None
        if session.recorder is not None:
            recorder, session.recorder = session.recorder, None
            with contextlib.suppress(Exception):
                recorder.stop()

    def _fresh(self, session: _Session) -> bool:
        return self._session is session and not session.cancelled.is_set() and not self._closed

    def start(self) -> dict:
        with self._lock:
            if self._closed:
                message = "Voice controller is closed"
                raise RuntimeError(message)
            if self._session is not None:
                self._session.cancelled.set()
                self._release(self._session)
            session = _Session()
            self._session = session
            try:
                # Barge-in stops speech/task output before opening the input.
                self._interrupt()
                session.recorder = self._recorder_factory()
                session.recorder.start()
            except Exception:
                self._release(session)
                session.state = "error"
                session.error = "Microphone unavailable. Check your audio input."
                return self._emit()
            session.timer = threading.Timer(MAX_HOLD_SECONDS, self.stop, args=(session.id,))
            session.timer.daemon = True
            session.timer.start()
            return self._emit()

    def stop(self, session_id: str | None = None) -> dict:
        with self._lock:
            session = self._session
            if (
                session is None
                or (session_id is not None and session.id != session_id)
                or session.state != "listening"
            ):
                return self.snapshot()
            recorder = session.recorder
            if session.timer is not None:
                session.timer.cancel()
                session.timer = None
            session.recorder = None
            if recorder is None:
                session.state = "error"
                session.error = "Microphone unavailable. Check your audio input."
                return self._emit()
            try:
                recorder.stop()
                audio = recorder.wav_bytes()
            except Exception:
                if recorder is not None:
                    with contextlib.suppress(Exception):
                        recorder.stop()
                session.state = "error"
                session.error = "Could not finish recording. Please try again."
                return self._emit()
            session.state = "transcribing"
            result = self._emit()
            worker = threading.Thread(target=self._process, args=(session, audio), daemon=True)
            self._workers.add(worker)
            worker.start()
            return result

    def _process(self, session: _Session, audio: bytes) -> None:
        try:
            text = self._transcribe(audio) if audio else None
            text = text.strip() if isinstance(text, str) else ""
            # Waiting for cancellation of the preceding task must not block
            # new start/cancel calls or hold the controller's state lock.
            ready = self._wait_until_ready(session.cancelled) if text else True
            with self._lock:
                if not self._fresh(session):
                    return
                if not text:
                    session.state = "error"
                    session.error = "I didn't catch that. Hold while speaking."
                elif not ready:
                    session.state = "error"
                    session.error = (
                        "Thursday is still stopping the previous task. Please try again."
                    )
                else:
                    session.transcript = text
                    try:
                        # The submit callback only reserves and starts work;
                        # it must not wait for task completion under this lock.
                        accepted = self._submit(text)
                        session.task_id = accepted.get("task_id")
                        session.conversation_id = accepted.get("conversation_id")
                        session.state = "submitted"
                    except Exception:
                        session.state = "error"
                        session.error = "Thursday could not start that request. Please try again."
                self._emit()
        except Exception:
            with self._lock:
                if self._fresh(session):
                    session.state = "error"
                    session.error = "Could not transcribe that. Please try again."
                    self._emit()
        finally:
            with self._lock:
                self._workers.discard(threading.current_thread())

    def cancel(self, session_id: str | None = None) -> dict:
        with self._lock:
            session = self._session
            if session is None or (session_id is not None and session.id != session_id):
                return self.snapshot()
            session.cancelled.set()
            self._release(session)
            session.state = "cancelled"
            session.transcript = ""
            session.error = ""
            with contextlib.suppress(Exception):
                self._interrupt()
            return self._emit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.cancel()
                self._closed = True
            workers = list(self._workers)
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(timeout=1)
