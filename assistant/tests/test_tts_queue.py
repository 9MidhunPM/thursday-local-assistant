"""Exercise the speech queue using asynchronous in-memory provider fixtures."""

import asyncio
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import assistant.voice.tts as tts_module
from assistant.voice.tts import EdgeTTS, TTSQueuePriority


def until(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    message = "Speech worker did not reach its expected state"
    raise AssertionError(message)


@pytest.fixture
def speech(monkeypatch, tmp_path):
    started, audio, lifecycle = [], [], []
    holds: dict[str, threading.Event] = {}
    cancelled = threading.Event()
    failures: set[str] = set()
    resist_cancel: set[str] = set()

    class Communicate:
        def __init__(self, text, _voice, **_settings):
            self.text = text

        async def save(self, path):
            started.append(self.text)
            if self.text in failures:
                message = "synthetic private provider failure"
                raise RuntimeError(message)
            try:
                while self.text in holds and not holds[self.text].is_set():
                    await asyncio.sleep(0.001)
            except asyncio.CancelledError:
                cancelled.set()
                if self.text not in resist_cancel:
                    raise
                while not holds[self.text].is_set():
                    await asyncio.sleep(0.001)
            Path(path).write_bytes(b"synthetic mp3 fixture")

    monkeypatch.setitem(sys.modules, "edge_tts", SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(EdgeTTS, "_find_edge_tts", staticmethod(lambda: "fixture-edge-tts"))
    monkeypatch.setattr(tts_module, "runtime_dir", lambda _name: tmp_path)
    monkeypatch.setattr(tts_module, "IDLE_GRACE_SECONDS", 0.02)
    tts = EdgeTTS(
        on_speak_start=lambda: lifecycle.append("start"),
        on_speak_end=lambda: lifecycle.append("end"),
        on_audio_ready=lambda filename, text: audio.append((filename, text)),
    )
    yield SimpleNamespace(
        tts=tts, started=started, audio=audio, lifecycle=lifecycle,
        holds=holds, failures=failures, cancelled=cancelled,
        resist_cancel=resist_cancel, directory=tmp_path,
    )
    tts.stop()
    for release in holds.values():
        release.set()
    until(lambda: not tts._syntheses)


def test_equal_priority_sentences_are_fifo_and_files_are_private(speech):
    speech.holds["First sentence."] = threading.Event()
    speech.tts.speak_async("First sentence.")
    until(lambda: speech.started == ["First sentence."])
    speech.tts.speak_async("Zebra follows.")
    speech.tts.speak_async("Apple comes last.")
    speech.holds["First sentence."].set()
    until(lambda: not speech.tts._queue_active)
    assert [text for _, text in speech.audio] == [
        "First sentence.", "Zebra follows.", "Apple comes last."
    ]
    assert speech.lifecycle == ["start", "end"]
    for filename, _ in speech.audio:
        assert stat.S_IMODE((speech.directory / filename).stat().st_mode) == 0o600


def test_priority_reorders_waiting_items_without_reversing_equal_priorities(speech):
    speech.holds["First"] = threading.Event()
    speech.tts.speak_async("First")
    until(lambda: speech.started == ["First"])
    speech.tts.speak_async("Normal first")
    speech.tts.speak_async("Urgent", TTSQueuePriority.HIGH)
    speech.tts.speak_async("Normal second")
    speech.holds["First"].set()
    until(lambda: not speech.tts._queue_active)
    assert [text for _, text in speech.audio] == [
        "First", "Urgent", "Normal first", "Normal second"
    ]


def test_stop_cancels_inflight_and_drops_pending_old_sentences(speech):
    speech.holds["Old active"] = threading.Event()
    speech.tts.speak_async("Old active")
    until(lambda: speech.started == ["Old active"])
    speech.tts.speak_async("Old queued")
    speech.tts.stop()
    assert speech.cancelled.wait(1)
    speech.tts.speak_async("New response")
    until(lambda: speech.audio and not speech.tts._queue_active)
    assert [text for _, text in speech.audio] == ["New response"]
    assert "Old queued" not in speech.started
    assert speech.lifecycle == ["start", "start", "end"]


def test_late_cancel_resistant_synthesis_never_emits_stale_audio(speech):
    speech.holds["Stale"] = threading.Event()
    speech.resist_cancel.add("Stale")
    speech.tts.speak_async("Stale")
    until(lambda: speech.started == ["Stale"])
    speech.tts.stop()
    assert speech.cancelled.wait(1)
    speech.tts.speak_async("Current")
    until(lambda: speech.audio and not speech.tts._queue_active)
    speech.holds["Stale"].set()
    until(lambda: not speech.tts._syntheses)
    # Let the old worker complete its stale-file cleanup as well.
    until(lambda: len(list(speech.directory.glob("*.mp3"))) == 1)
    assert [text for _, text in speech.audio] == ["Current"]
    assert speech.lifecycle == ["start", "start", "end"]


def test_idle_worker_finishes_then_restarts_for_later_sentence(speech):
    speech.tts.speak_async("First batch")
    until(lambda: speech.lifecycle == ["start", "end"])
    assert not speech.tts._queue_active
    assert speech.tts._worker is None
    speech.tts.speak_async("Later batch")
    until(lambda: speech.lifecycle == ["start", "end", "start", "end"])
    assert [text for _, text in speech.audio] == ["First batch", "Later batch"]
    assert not speech.tts._queue_active


def test_completion_callback_can_enqueue_without_losing_new_sentence(speech):
    def completed():
        speech.lifecycle.append("end")
        if len(speech.audio) == 1:
            speech.tts.speak_async("Added at completion")

    speech.tts._on_speak_end = completed
    speech.tts.speak_async("Original")
    until(lambda: len(speech.audio) == 2 and not speech.tts._queue_active)
    assert [text for _, text in speech.audio] == ["Original", "Added at completion"]
    assert speech.lifecycle == ["start", "end", "start", "end"]


def test_completion_callback_can_stop_and_replace_queue(speech):
    def completed():
        speech.lifecycle.append("end")
        if len(speech.audio) == 1:
            speech.tts.stop()
            speech.tts.speak_async("Replacement")

    speech.tts._on_speak_end = completed
    speech.tts.speak_async("Original")
    until(lambda: speech.lifecycle == ["start", "end", "start", "end"])
    assert [text for _, text in speech.audio] == ["Original", "Replacement"]
    assert not speech.tts._queue_active
    assert speech.tts._worker is None


def test_synthesis_failure_does_not_leave_active_state_or_log_provider_content(speech, caplog):
    speech.failures.add("Broken")
    speech.tts.speak_async("Broken")
    until(lambda: speech.lifecycle == ["start", "end"])
    assert not speech.tts._queue_active
    assert speech.audio == []
    assert "private provider" not in caplog.text
    assert list(speech.directory.glob("*.mp3")) == []


@pytest.mark.parametrize(("source", "expected"), [
    ("**Ready.** Read [the guide](https://example.test/private).", "Ready. Read the guide."),
    ("Use `secret_token` carefully. https://example.test/private", "Use carefully."),
    ("Before. ```python\nprivate_code()\n``` After.", "Before. After."),
    ("Before. ```python\nprivate_code()", "Before."),
    ("See ![private image](https://example.test/img) www.example.test/private", "See"),
])
def test_spoken_text_omits_code_and_urls(source, expected):
    assert EdgeTTS._clean_text(source) == expected


def test_markup_only_chunk_does_not_start_worker(speech):
    speech.tts.speak_async("```python\nprivate_code()\n``` https://example.test")
    assert speech.tts._worker is None
    assert speech.lifecycle == []
    assert speech.audio == []
