"""Speech filtering must retain Markdown context across model stream chunks."""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

from assistant import server
from assistant.tests.test_task_http import fixture_runtime
from assistant.voice.tts import EdgeTTS


def test_streamed_code_stays_silent_and_trailing_prose_is_spoken(monkeypatch):
    spoken = []
    tts = SimpleNamespace(_clean_text=EdgeTTS._clean_text, speak_async=spoken.append)

    def handle(_prompt, *, on_stream, **_kwargs):
        chunks = ["Here is the code: ```python\n", "fixture_function()\n", "``` Done."]
        for chunk in chunks:
            on_stream(chunk)
            # Exercise the actual debounce worker between opening/code/closing.
            time.sleep(0.45)
        return "".join(chunks)

    logger = logging.getLogger("fixture.speech")
    runtime = SimpleNamespace(
        tts=tts,
        agent=SimpleNamespace(handle_message=handle),
        loggers=SimpleNamespace(model=logger, error=logger),
    )
    monkeypatch.setattr(server, "server_runtime", runtime)
    monkeypatch.setattr(server, "broadcaster", server.EventBroadcaster())
    monkeypatch.setattr(server, "playback_generation", None)
    monkeypatch.setattr(server, "playback_client", "")
    monkeypatch.setattr(server, "is_busy", False)
    server.ThursdayHTTPRequestHandler.run_agent(None, "Fixture", True, client_id="native")
    assert spoken == ["Here is the code: Done."]


def test_new_silent_request_retires_previous_playback(monkeypatch, tmp_path):
    runtime = fixture_runtime(tmp_path)
    monkeypatch.setattr(server, "server_runtime", runtime)
    monkeypatch.setattr(server, "broadcaster", server.EventBroadcaster())
    monkeypatch.setattr(server, "active_conversation_id", runtime.agent.start_conversation())
    monkeypatch.setattr(server, "is_busy", False)
    monkeypatch.setattr(server, "tts_active", True)
    monkeypatch.setattr(server, "playback_generation", "previous")
    monkeypatch.setattr(server, "playback_client", "native")
    server.accept_message("Silent fixture", False)
    assert server.playback_generation is None
    assert server.tts_active is False
    deadline = time.monotonic() + 3
    while server.is_busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not server.is_busy
    runtime.agent.close()
