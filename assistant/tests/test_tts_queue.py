"""Exercise the speech queue using asynchronous in-memory provider fixtures."""

import asyncio
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import assistant.voice.neural_tts as neural_module
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
    started, audio, lifecycle, statuses, local_started = [], [], [], [], []
    holds: dict[str, threading.Event] = {}
    cancelled = threading.Event()
    failures: set[str] = set()
    resist_cancel: set[str] = set()
    local_enabled = set()

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

    async def local(text, path, **_kwargs):
        local_started.append(text)
        if text not in local_enabled:
            message = "synthetic private local provider failure"
            raise RuntimeError(message)
        path.write_bytes(b"synthetic local mp3 fixture")

    monkeypatch.setattr(EdgeTTS, "_generate_local", staticmethod(local))

    async def encode(source, path):
        path.write_bytes(source)

    monkeypatch.setattr(EdgeTTS, "_encode_audio", staticmethod(encode))
    tts = EdgeTTS(
        engine="online",
        on_speak_start=lambda: lifecycle.append("start"),
        on_speak_end=lambda: lifecycle.append("end"),
        on_audio_ready=lambda filename, text: audio.append((filename, text)),
        on_speech_status=statuses.append,
    )
    yield SimpleNamespace(
        tts=tts, started=started, audio=audio, lifecycle=lifecycle,
        holds=holds, failures=failures, cancelled=cancelled,
        resist_cancel=resist_cancel, directory=tmp_path,
        local_started=local_started, local_enabled=local_enabled, statuses=statuses,
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
    assert speech.statuses[-1]["state"] == "unavailable"
    assert "RuntimeError" in caplog.text
    assert "elapsed_ms=" in caplog.text


@pytest.mark.parametrize(("error", "expected"), [
    (TimeoutError("private text https://private.invalid/token"), "timeout"),
    (ConnectionError("private credentials"), "ConnectionError"),
])
def test_provider_failure_classification_omits_private_payload(error, expected):
    assert tts_module._provider_failure(error) == expected


def test_provider_failure_classification_prefers_only_http_status():
    class ProviderError(Exception):
        status = 403

    assert tts_module._provider_failure(ProviderError("private response payload")) == "http_403"


@pytest.mark.parametrize(("status", "expected"), [(401, 300), (403, 300), (429, 60), (503, 15)])
def test_provider_retry_delay_depends_on_failure_type(status, expected):
    error = RuntimeError("private payload")
    error.status = status
    assert tts_module._provider_retry_seconds(error) == expected


def test_transient_timeout_does_not_disable_online_voice_for_five_minutes():
    assert tts_module._provider_retry_seconds(TimeoutError("private payload")) == 15


def test_cloud_rejection_uses_local_speech_and_cools_down_subsequent_chunks(speech):
    speech.failures.add("First cloud rejection")
    speech.local_enabled.update({"First cloud rejection", "Next sentence"})
    speech.tts.speak_async("First cloud rejection")
    speech.tts.speak_async("Next sentence")
    until(lambda: not speech.tts._queue_active)
    assert speech.started == ["First cloud rejection"]
    assert speech.local_started == ["First cloud rejection", "Next sentence"]
    assert [text for _, text in speech.audio] == ["First cloud rejection", "Next sentence"]
    assert [status["state"] for status in speech.statuses] == ["fallback"]
    assert speech.lifecycle == ["start", "end"]


def test_cloud_is_retried_after_cooldown_and_clears_fallback_status(speech):
    speech.failures.add("Temporarily rejected")
    speech.local_enabled.add("Temporarily rejected")
    speech.tts.speak_async("Temporarily rejected")
    until(lambda: not speech.tts._queue_active)
    speech.tts._cloud_retry_after = 0
    speech.tts.speak_async("Provider recovered")
    until(lambda: len(speech.audio) == 2 and not speech.tts._queue_active)
    assert speech.started == ["Temporarily rejected", "Provider recovered"]
    assert [status["state"] for status in speech.statuses] == ["fallback", "ready"]


def test_processing_failure_does_not_mark_healthy_online_provider_unavailable(speech, monkeypatch):
    async def broken_encoder(_source, _path):
        message = "Fixture encoder failed"
        raise RuntimeError(message)

    monkeypatch.setattr(EdgeTTS, "_encode_audio", staticmethod(broken_encoder))
    speech.tts.speak_async("Provider succeeds")
    until(lambda: not speech.tts._queue_active)
    assert speech.started == ["Provider succeeds"]
    assert speech.local_started == []
    assert speech.tts._cloud_retry_after == 0
    assert speech.statuses[-1]["state"] == "unavailable"


def test_explicit_neural_skips_online_dependency_probe(monkeypatch):
    def unavailable():
        message = "Online dependency must not be probed for local speech"
        raise AssertionError(message)

    monkeypatch.setattr(EdgeTTS, "_find_edge_tts", staticmethod(unavailable))
    tts = EdgeTTS(engine="neural")
    assert tts._engine == "neural"
    assert tts._local_voice == "bm_george"
    assert not tts._emergency_fallback


def test_online_is_default_and_uses_dependency_preflight(monkeypatch):
    probes = []
    monkeypatch.setattr(
        EdgeTTS, "_find_edge_tts", staticmethod(lambda: probes.append(True) or "fixture"),
    )
    tts = EdgeTTS()
    assert tts._engine == "online"
    assert tts._voice == "en-GB-RyanNeural"
    assert tts._rate == "+0%"
    assert probes == [True]


def test_close_releases_own_executor_without_poisoning_next_instance(monkeypatch):
    closed = []
    monkeypatch.setattr(neural_module, "close_worker", lambda: closed.append(True))
    first = EdgeTTS(engine="neural")
    first.close()
    first.speak_async("Must not be queued after close")
    assert first._speech_queue.empty()
    assert first._executor._shutdown
    second = EdgeTTS(engine="neural")
    assert second._executor.submit(lambda: "ready").result(timeout=1) == "ready"
    second.close()
    assert closed == [True, True]


def test_neural_default_skips_cloud_and_reports_ready(speech):
    speech.tts._engine = "neural"
    speech.local_enabled.add("Natural local speech")
    speech.tts.speak_async("Natural local speech")
    until(lambda: not speech.tts._queue_active)
    assert speech.started == []
    assert speech.local_started == ["Natural local speech"]
    assert [status["state"] for status in speech.statuses] == ["ready"]


@pytest.mark.parametrize("opt_in", [False, True])
def test_basic_robotic_voice_requires_explicit_opt_in(speech, monkeypatch, opt_in):
    speech.tts._engine = "neural"
    speech.tts._emergency_fallback = opt_in
    calls = []

    async def local(_text, path, *, emergency=False, **_kwargs):
        calls.append(emergency)
        if not emergency:
            message = "Private neural failure payload"
            raise RuntimeError(message)
        path.write_bytes(b"emergency fixture")

    monkeypatch.setattr(EdgeTTS, "_generate_local", staticmethod(local))
    speech.tts.speak_async("Fixture")
    until(lambda: not speech.tts._queue_active)
    assert speech.started == []
    if opt_in:
        assert calls == [False, True]
        assert len(speech.audio) == 1
        assert speech.statuses[-1]["state"] == "fallback"
        assert "optional basic voice" in speech.statuses[-1]["message"]
    else:
        assert calls == [False]
        assert speech.audio == []
        assert speech.statuses[-1]["state"] == "unavailable"


@pytest.mark.asyncio
async def test_local_encoding_uses_pipes_and_private_output(monkeypatch, tmp_path):
    calls = []

    class Encoder:
        returncode = 0

        async def communicate(self, wav):
            assert wav == b"synthetic wav"
            return b"synthetic mp3", b""

    async def create(*args, **_kwargs):
        calls.append(args)
        return Encoder()

    monkeypatch.setitem(sys.modules, "assistant.voice.neural_tts", SimpleNamespace(
        synthesize_wav=lambda _text, **_kwargs: b"synthetic wav",
    ))
    monkeypatch.setattr(tts_module.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    path = tmp_path / "speech.mp3"
    await EdgeTTS._generate_local("private spoken text", path)
    assert asyncio.get_running_loop()._default_executor is None
    assert path.read_bytes() == b"synthetic mp3"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "private spoken text" not in repr(calls)
    assert "pipe:0" in calls[0]
    assert "pipe:1" in calls[0]
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in calls[0]


@pytest.mark.asyncio
async def test_cancel_local_synthesis_signals_worker_without_starting_encoder(
    monkeypatch, tmp_path,
):
    entered, finished = threading.Event(), threading.Event()

    def synthesize(_text, *, cancelled, **_kwargs):
        entered.set()
        cancelled.wait(2)
        if cancelled.is_set():
            finished.set()
        return b"synthetic wav"

    async def create(*_args, **_kwargs):
        message = "Cancelled synthesis must not reach the encoder"
        raise AssertionError(message)

    monkeypatch.setitem(sys.modules, "assistant.voice.neural_tts", SimpleNamespace(
        synthesize_wav=synthesize,
    ))
    monkeypatch.setattr(tts_module.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    task = asyncio.create_task(EdgeTTS._generate_local("Fixture", tmp_path / "speech.mp3"))
    for _ in range(200):
        if entered.is_set():
            break
        await asyncio.sleep(0.005)
    assert entered.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(200):
        if finished.is_set():
            break
        await asyncio.sleep(0.005)
    assert finished.is_set()
    assert not (tmp_path / "speech.mp3").exists()


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
