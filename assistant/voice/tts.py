from __future__ import annotations

import os
import queue as _queue_mod
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any


from assistant.voice.base import TextToSpeech


class TTSQueuePriority(IntEnum):
    """Priority levels for TTS queue. Higher = more urgent."""
    LOW = 0
    NORMAL = 50
    HIGH = 100
    INTERRUPT = 200  # Barge-in: stop current, clear queue, play immediately


@dataclass(order=True)
class TTSQueueItem:
    priority: int
    text: str = field(compare=False)
    timestamp: float = field(compare=False, default_factory=time.time)
    item_id: str = field(compare=False, default_factory=lambda: uuid.uuid4().hex[:8])


class EdgeTTS(TextToSpeech):
    def __init__(
        self,
        voice: str = "en-US-EmmaMultilingualNeural",
        rate: str = "+25%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        mpv_profile: str = "low-latency",
        on_speak_start: Any | None = None,
        on_speak_end: Any | None = None,
        on_audio_ready: Any | None = None,
    ) -> None:
        self._voice = voice
        self._rate = rate
        self._volume = volume
        self._pitch = pitch
        self._mpv_profile = mpv_profile
        self._edge_tts_bin = self._find_edge_tts()
        self._current_process: subprocess.Popen[Any] | None = None
        self._lock = threading.Lock()
        self._on_speak_start = on_speak_start
        self._on_speak_end = on_speak_end
        self._on_audio_ready = on_audio_ready

        # Priority queue for sequential playback with barge-in support
        self._speech_queue: _queue_mod.PriorityQueue[TTSQueueItem] = _queue_mod.PriorityQueue()
        self._queue_active = False
        self._stop_event = threading.Event()
        self._interrupt_event = threading.Event()  # For barge-in
        self._current_item: TTSQueueItem | None = None

    @staticmethod
    def _find_edge_tts() -> str:
        candidates = [
            shutil.which("edge-tts"),
            os.path.expanduser("~/.local/bin/edge-tts"),
        ]
        for candidate in candidates:
            if candidate:
                return candidate
        raise FileNotFoundError(
            "edge-tts not found. Install it via: pip install edge-tts"
        )

    def _build_cmd(self, text: str, output_path: str | None = None) -> list[str]:
        cmd = [
            self._edge_tts_bin,
            "--voice", self._voice,
            "--rate", self._rate,
            "--volume", self._volume,
            "--pitch", self._pitch,
            "--text", text,
        ]
        if output_path:
            cmd.extend(["--write-media", output_path])
        return cmd

    def synthesize(self, text: str, output_path: str) -> None:
        self.stop()
        cmd = self._build_cmd(text, output_path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"TTS failed: {result.stderr.strip()}")

    def speak(self, text: str) -> None:
        if not shutil.which("mpv"):
            raise FileNotFoundError("mpv is required for playback.")
        clean_text = self._clean_text(text)
        if not clean_text.strip():
            return
        self.stop()
        quoted_text = shlex.quote(clean_text.strip())
        tts_cmd = (
            f"{self._edge_tts_bin} --voice {shlex.quote(self._voice)} "
            f"--rate {shlex.quote(self._rate)} --text {quoted_text} "
            f"| mpv --profile={shlex.quote(self._mpv_profile)} --untimed --no-cache -"
        )
        subprocess.run(
            tts_cmd,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )

    def _kill_process_group(self, proc: subprocess.Popen[Any]) -> None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, 15)
            proc.wait(timeout=2)
        except Exception:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, 9)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def stop(self) -> None:
        """Stop all speech: clear queue, kill current process."""
        self._stop_event.set()
        self._interrupt_event.set()
        # Drain the queue
        while True:
            try:
                self._speech_queue.get_nowait()
            except _queue_mod.Empty:
                break
        # Kill any currently generating process
        with self._lock:
            if self._current_process and self._current_process.poll() is None:
                self._kill_process_group(self._current_process)
                self._current_process = None

    def speak_async(self, text: str, priority: TTSQueuePriority = TTSQueuePriority.NORMAL) -> None:
        """Queue text for sequential audio generation and playback with priority."""
        clean_text = self._clean_text(text)
        if not clean_text.strip():
            return

        item = TTSQueueItem(
            priority=-priority.value,  # Negative because PriorityQueue is min-heap
            text=clean_text.strip(),
        )
        self._speech_queue.put(item)
        self._ensure_queue_processor()

    def interrupt(self, text: str, priority: TTSQueuePriority = TTSQueuePriority.INTERRUPT) -> None:
        """Barge-in: immediately stop current speech, clear queue, and play new text."""
        self._interrupt_event.set()
        # Signal stop to queue processor
        self._stop_event.set()
        # Wait briefly for queue processor to stop
        time.sleep(0.05)
        # Clear the queue
        while True:
            try:
                self._speech_queue.get_nowait()
            except _queue_mod.Empty:
                break
        # Kill current generation if any
        with self._lock:
            if self._current_process and self._current_process.poll() is None:
                self._kill_process_group(self._current_process)
                self._current_process = None
        # Reset events for new speech
        self._stop_event.clear()
        self._interrupt_event.clear()
        # Queue new high-priority item
        self.speak_async(text, priority=priority)

    def _ensure_queue_processor(self) -> None:
        with self._lock:
            if not self._queue_active:
                self._queue_active = True
                self._stop_event.clear()
                self._interrupt_event.clear()
                threading.Thread(
                    target=self._process_speech_queue, daemon=True
                ).start()

    def _process_speech_queue(self) -> None:
        """Background thread: picks text from queue, generates mp3,
        fires on_audio_ready callback with the filename."""
        tmp_dir = Path("/tmp/thursday_tts")
        tmp_dir.mkdir(parents=True, exist_ok=True)

        fired_start = False

        try:
            while not self._stop_event.is_set():
                try:
                    # Wait for next item with short timeout to check stop event
                    item: TTSQueueItem = self._speech_queue.get(timeout=0.2)
                except _queue_mod.Empty:
                    continue

                if self._stop_event.is_set():
                    break

                # Check for interrupt (barge-in)
                if self._interrupt_event.is_set():
                    break

                self._current_item = item

                if not fired_start:
                    if self._on_speak_start:
                        self._on_speak_start()
                    fired_start = True

                filename = f"tts_{item.item_id}.mp3"
                filepath = tmp_dir / filename

                # Build and run the TTS generation synchronously
                quoted_text = shlex.quote(item.text)
                tts_cmd = (
                    f"{self._edge_tts_bin}"
                    f" --voice {shlex.quote(self._voice)}"
                    f" --rate {shlex.quote(self._rate)}"
                    f" --text {quoted_text}"
                    f" --write-media {shlex.quote(str(filepath))}"
                )

                proc = subprocess.Popen(
                    tts_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )
                with self._lock:
                    self._current_process = proc

                proc.wait()

                with self._lock:
                    if self._current_process == proc:
                        self._current_process = None

                # Check for interrupt after generation
                if self._interrupt_event.is_set() or self._stop_event.is_set():
                    if filepath.exists():
                        filepath.unlink(missing_ok=True)
                    break

                # Notify that audio is ready
                if filepath.exists() and self._on_audio_ready:
                    self._on_audio_ready(filename, item.text)

        finally:
            with self._lock:
                self._queue_active = False
                self._current_process = None
                self._current_item = None
            if fired_start and self._on_speak_end:
                self._on_speak_end()

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"[*_`#\[\]]", "", text)
        text = re.sub(r"\[.*?\]\(.*?\)", "", text)
        return text.strip()

    @staticmethod
    def list_voices() -> list[dict[str, str]]:
        edge_tts_bin = EdgeTTS._find_edge_tts()
        result = subprocess.run(
            [edge_tts_bin, "--list-voices"],
            capture_output=True, text=True, timeout=15,
        )
        voices: list[dict[str, str]] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(maxsplit=1)
            if parts:
                voices.append({"name": parts[0], "info": parts[1] if len(parts) > 1 else ""})
        return voices