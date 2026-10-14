from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from assistant.paths import purge_runtime_files, runtime_dir
from assistant.voice.base import TextToSpeech

if TYPE_CHECKING:
    from collections.abc import Callable

_log = logging.getLogger("assistant.voice")
IDLE_GRACE_SECONDS = 0.2


class TTSQueuePriority(IntEnum):
    """Higher priorities run first; equal priorities preserve sentence order."""

    LOW = 0
    NORMAL = 50
    HIGH = 100
    INTERRUPT = 200


@dataclass(order=True)
class TTSQueueItem:
    priority: int
    sequence: int
    text: str = field(compare=False)
    item_id: str = field(compare=False, default_factory=lambda: uuid.uuid4().hex)


class EdgeTTS(TextToSpeech):
    def __init__(
        self,
        voice: str = "en-GB-RyanNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        mpv_profile: str = "low-latency",
        on_speak_start: Callable[[], None] | None = None,
        on_speak_end: Callable[[], None] | None = None,
        on_audio_ready: Callable[[str, str], None] | None = None,
    ) -> None:
        self._voice, self._rate = voice, rate
        self._volume, self._pitch = volume, pitch
        self._mpv_profile = mpv_profile
        self._edge_tts_bin = self._find_edge_tts()
        self._lock = threading.RLock()
        self._on_speak_start, self._on_speak_end = on_speak_start, on_speak_end
        self._on_audio_ready = on_audio_ready
        self._speech_queue: queue.PriorityQueue[TTSQueueItem] = queue.PriorityQueue()
        self._queue_active = False
        self._queue_wake = threading.Event()
        self._worker: threading.Thread | None = None
        self._sequence = itertools.count()
        self._generation = 0
        self._syntheses: set[tuple[asyncio.AbstractEventLoop, asyncio.Task]] = set()
        self._playback_process: subprocess.Popen | None = None

    @staticmethod
    def _find_edge_tts() -> str:
        candidates = [
            str(Path(sys.executable).parent / "edge-tts"),
            shutil.which("edge-tts"),
            str(Path("~/.local/bin/edge-tts").expanduser()),
        ]
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if not shutil.which(candidate) and not Path(candidate).is_file():
                continue
            try:
                probe = subprocess.run(
                    [candidate, "--version"], capture_output=True, text=True, timeout=3
                )
                if probe.returncode == 0:
                    return candidate
            except (OSError, subprocess.SubprocessError):
                continue
        message = "edge-tts not found or not functional. Install it via: pip install edge-tts"
        raise FileNotFoundError(message)

    async def _generate(self, text: str, path: Path, generation: int) -> None:
        import edge_tts  # noqa: PLC0415 - optional speech dependency

        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        with self._lock:
            if generation != self._generation:
                raise asyncio.CancelledError
            active = (loop, task)
            self._syntheses.add(active)
        try:
            await asyncio.wait_for(
                edge_tts.Communicate(
                    text, self._voice, rate=self._rate, volume=self._volume, pitch=self._pitch
                ).save(str(path)),
                timeout=60,
            )
            path.chmod(0o600)
        finally:
            with self._lock:
                self._syntheses.discard(active)

    def synthesize(self, text: str, output_path: str) -> None:
        """Synthesize without exposing private speech in process arguments."""
        with self._lock:
            generation = self._generation
        path = Path(output_path)
        try:
            asyncio.run(self._generate(self._clean_text(text), path, generation))
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def speak(self, text: str) -> None:
        """Blocking local playback for CLI callers; stop() can interrupt it."""
        if not shutil.which("mpv"):
            message = "mpv is required for playback."
            raise FileNotFoundError(message)
        clean_text = self._clean_text(text)
        if not clean_text:
            return
        self.stop()
        with self._lock:
            generation = self._generation
        path = runtime_dir("tts") / f"tts_{uuid.uuid4().hex}.mp3"
        process = None
        try:
            asyncio.run(self._generate(clean_text, path, generation))
            with self._lock:
                if generation != self._generation:
                    return
                process = subprocess.Popen(
                    ["mpv", "--no-video", f"--profile={self._mpv_profile}", str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._playback_process = process
            process.wait(timeout=120)
        except asyncio.CancelledError:
            pass
        finally:
            if process is not None:
                with contextlib.suppress(OSError):
                    process.terminate()
                with self._lock:
                    if self._playback_process is process:
                        self._playback_process = None
            path.unlink(missing_ok=True)

    def stop(self) -> None:
        """Invalidate queued/in-flight speech before accepting replacement text."""
        with self._lock:
            self._generation += 1
            self._queue_active = False
            self._worker = None
            while True:
                try:
                    self._speech_queue.get_nowait()
                except queue.Empty:
                    break
            self._queue_wake.set()
            for loop, task in tuple(self._syntheses):
                # An event loop can close between its state check and delivery.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(task.cancel)
            if self._playback_process is not None:
                with contextlib.suppress(OSError):
                    self._playback_process.terminate()
                self._playback_process = None

    def speak_async(self, text: str, priority: TTSQueuePriority = TTSQueuePriority.NORMAL) -> None:
        clean_text = self._clean_text(text)
        if not clean_text:
            return
        with self._lock:
            self._speech_queue.put(TTSQueueItem(-priority.value, next(self._sequence), clean_text))
            self._queue_wake.set()
            self._ensure_queue_processor()

    def interrupt(self, text: str, priority: TTSQueuePriority = TTSQueuePriority.INTERRUPT) -> None:
        self.stop()
        self.speak_async(text, priority=priority)

    def _ensure_queue_processor(self) -> None:
        with self._lock:
            if self._worker is not None:
                return
            self._queue_active = True
            self._worker = threading.Thread(
                target=self._process_speech_queue,
                args=(self._generation, self._on_speak_start,
                      self._on_audio_ready, self._on_speak_end),
                daemon=True,
            )
            self._worker.start()

    def _process_speech_queue(self, generation, on_start, on_audio, on_end) -> None:
        fired_start = False
        waited = False
        try:
            tmp_dir = runtime_dir("tts")
            purge_runtime_files(tmp_dir)
            while True:
                with self._lock:
                    if generation != self._generation:
                        return
                    try:
                        # Dequeue and generation validation are one operation.
                        # A stale worker never puts old speech into a new queue.
                        item = self._speech_queue.get_nowait()
                    except queue.Empty:
                        if waited:
                            return
                        self._queue_wake.clear()
                        item = None
                    if item is not None and not fired_start:
                        fired_start = True
                        if on_start:
                            on_start()
                if item is None:
                    self._queue_wake.wait(IDLE_GRACE_SECONDS)
                    waited = True
                    continue
                waited = False
                path = tmp_dir / f"tts_{item.item_id}.mp3"
                try:
                    asyncio.run(self._generate(item.text, path, generation))
                except asyncio.CancelledError:
                    path.unlink(missing_ok=True)
                    return
                except Exception:
                    path.unlink(missing_ok=True)
                    _log.warning("Speech synthesis failed; retry or check the speech connection.")
                    continue
                with self._lock:
                    if generation != self._generation:
                        path.unlink(missing_ok=True)
                        return
                    if on_audio:
                        on_audio(path.name, item.text)
                    else:
                        path.unlink(missing_ok=True)
        except Exception:
            _log.warning("Speech queue failed; retry or check the speech connection.")
        finally:
            with self._lock:
                if self._worker is threading.current_thread():
                    if fired_start and generation == self._generation and on_end:
                        try:
                            on_end()
                        except Exception:
                            _log.warning("Speech completion delivery failed")
                    # A completion callback can itself stop and replace speech.
                    # Never clear the replacement worker's ownership.
                    if self._worker is threading.current_thread():
                        self._worker = None
                        self._queue_active = False
                        if not self._speech_queue.empty():
                            self._ensure_queue_processor()

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"```.*?(?:```|$)", " ", text, flags=re.DOTALL)
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"(?:https?://|www\.)\S+", " ", text)
        text = re.sub(r"`[^`]*(?:`|$)", " ", text)
        text = re.sub(r"[*_`#\[\]<>]", "", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def list_voices() -> list[dict[str, str]]:
        result = subprocess.run(
            [EdgeTTS._find_edge_tts(), "--list-voices"],
            capture_output=True, text=True, timeout=15,
        )
        voices: list[dict[str, str]] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(maxsplit=1)
            if parts:
                voices.append({"name": parts[0], "info": parts[1] if len(parts) > 1 else ""})
        return voices
