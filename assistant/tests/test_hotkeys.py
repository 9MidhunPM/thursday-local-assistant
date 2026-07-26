"""Unit tests for the hotkey daemon's pure combo-tracking logic."""

import json

from assistant.hotkeys import (
    CHUNK,
    INTERIM_MAX_SECONDS,
    KEY_C,
    KEY_LEFTALT,
    KEY_LEFTMETA,
    KEY_RIGHTALT,
    KEY_RIGHTMETA,
    SAMPLE_RATE,
    VOICE_HUD_FILE,
    ComboTracker,
    parse_sse_event,
    remove_hud_file,
    reuse_interim_as_final,
    write_hud_file,
)


def _frames(seconds: float) -> int:
    return int(seconds * SAMPLE_RATE / CHUNK)

PRESS, RELEASE, REPEAT = 1, 0, 2


def test_hold_super_alt_starts_ptt():
    t = ComboTracker()
    assert t.handle(KEY_LEFTMETA, PRESS) is None
    assert t.handle(KEY_LEFTALT, PRESS) == "ptt_start"


def test_alt_then_super_also_starts_ptt():
    t = ComboTracker()
    assert t.handle(KEY_RIGHTALT, PRESS) is None
    assert t.handle(KEY_RIGHTMETA, PRESS) == "ptt_start"


def test_releasing_either_modifier_stops_ptt():
    t = ComboTracker()
    t.handle(KEY_LEFTMETA, PRESS)
    t.handle(KEY_LEFTALT, PRESS)
    assert t.handle(KEY_LEFTALT, RELEASE) == "ptt_stop"


def test_full_combo_cycle_can_repeat():
    t = ComboTracker()
    for _ in range(3):
        assert t.handle(KEY_LEFTMETA, PRESS) is None
        assert t.handle(KEY_LEFTALT, PRESS) == "ptt_start"
        assert t.handle(KEY_LEFTMETA, RELEASE) == "ptt_stop"
        assert t.handle(KEY_LEFTALT, RELEASE) is None


def test_autorepeat_does_not_retrigger():
    t = ComboTracker()
    t.handle(KEY_LEFTMETA, PRESS)
    assert t.handle(KEY_LEFTALT, PRESS) == "ptt_start"
    assert t.handle(KEY_LEFTALT, REPEAT) is None
    assert t.handle(KEY_LEFTMETA, REPEAT) is None
    assert t.ptt_active


def test_extra_keys_during_hold_do_not_stop():
    t = ComboTracker()
    t.handle(KEY_LEFTMETA, PRESS)
    t.handle(KEY_LEFTALT, PRESS)
    assert t.handle(KEY_C, PRESS) is None
    assert t.handle(KEY_C, RELEASE) is None
    assert t.ptt_active


def test_one_of_each_side_is_enough():
    # Both Alt keys held, releasing one keeps the combo alive.
    t = ComboTracker()
    t.handle(KEY_LEFTMETA, PRESS)
    t.handle(KEY_LEFTALT, PRESS)
    t.handle(KEY_RIGHTALT, PRESS)
    assert t.handle(KEY_LEFTALT, RELEASE) is None
    assert t.handle(KEY_RIGHTALT, RELEASE) == "ptt_stop"


def test_super_alone_does_nothing():
    t = ComboTracker()
    assert t.handle(KEY_LEFTMETA, PRESS) is None
    assert t.handle(KEY_C, PRESS) is None
    assert not t.ptt_active


# --- SSE parsing -----------------------------------------------------------


def test_parse_sse_event_token():
    line = 'data: {"type": "token", "data": {"chunk": "Hello"}, "timestamp": 1.0}'
    assert parse_sse_event(line) == ("token", {"chunk": "Hello"})


def test_parse_sse_event_final_response():
    line = 'data: {"type": "final_response", "data": {"content": "Done"}}'
    assert parse_sse_event(line) == ("final_response", {"content": "Done"})


def test_parse_sse_event_ignores_pings_and_garbage():
    assert parse_sse_event(": ping") is None
    assert parse_sse_event("") is None
    assert parse_sse_event("data: {not json") is None
    assert parse_sse_event("data: [1, 2]") is None
    assert parse_sse_event('data: {"no_type": true}') is None


# --- HUD state file --------------------------------------------------------


def test_hud_file_roundtrip():
    try:
        write_hud_file("answering", transcript="hi thursday", answer="Hello!")
        data = json.loads(VOICE_HUD_FILE.read_text())
        assert data["state"] == "answering"
        assert data["transcript"] == "hi thursday"
        assert data["answer"] == "Hello!"
        assert data["updated"] > 0
    finally:
        remove_hud_file()
    assert not VOICE_HUD_FILE.exists()


def test_remove_hud_file_idempotent():
    remove_hud_file()
    remove_hud_file()  # must not raise


# --- Interim reuse (skip slow final re-transcription) ----------------------


def test_reuse_interim_when_fresh_and_complete():
    # 5s hold, interim covered all of it, 0.5s of new audio -> reuse.
    assert (
        reuse_interim_as_final("hello there", _frames(4.5), _frames(5.0)) == "hello there"
    )


def test_no_reuse_when_new_audio_after_interim():
    # 2.5s of speech arrived after the last interim -> must re-transcribe.
    assert reuse_interim_as_final("hello", _frames(2.5), _frames(5.0)) is None


def test_no_reuse_when_hold_exceeds_interim_window():
    # 30s hold: interim only saw the tail -> full transcribe required.
    mark = _frames(29.0)
    total = _frames(30.0)
    assert total > _frames(INTERIM_MAX_SECONDS)
    assert reuse_interim_as_final("tail only", mark, total) is None


def test_no_reuse_without_interim():
    assert reuse_interim_as_final("", 0, _frames(3.0)) is None
