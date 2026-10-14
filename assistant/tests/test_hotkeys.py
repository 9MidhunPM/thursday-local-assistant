"""Unit tests for the hotkey daemon's pure combo-tracking logic."""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import assistant.hotkeys as hotkeys
from assistant.hotkeys import (
    KEY_C,
    KEY_LEFTALT,
    KEY_LEFTMETA,
    KEY_RIGHTALT,
    KEY_RIGHTMETA,
    ComboTracker,
    KeyboardCombos,
    VoiceClient,
)

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



def test_modifier_keys_from_separate_keyboards_do_not_form_combo():
    tracker = KeyboardCombos()
    assert tracker.handle("one", KEY_LEFTMETA, PRESS) is None
    assert tracker.handle("two", KEY_LEFTALT, PRESS) is None
    assert not tracker.active
    assert tracker.handle("one", KEY_LEFTALT, PRESS) == "ptt_start"
    assert tracker.handle("two", KEY_LEFTALT, RELEASE) is None
    assert tracker.active


def test_disconnect_cancels_active_combo_and_reconnect_starts_clean():
    tracker = KeyboardCombos()
    tracker.handle("one", KEY_LEFTMETA, PRESS)
    assert tracker.handle("one", KEY_LEFTALT, PRESS) == "ptt_start"
    assert tracker.remove("one") == "ptt_cancel"
    assert not tracker.active
    assert tracker.handle("one", KEY_LEFTALT, PRESS) is None
    assert tracker.handle("one", KEY_LEFTMETA, PRESS) == "ptt_start"


def test_two_complete_combos_keep_one_session_until_both_released():
    tracker = KeyboardCombos()
    for device in ("one", "two"):
        assert tracker.handle(device, KEY_LEFTMETA, PRESS) is None
        expected = "ptt_start" if device == "one" else None
        assert tracker.handle(device, KEY_LEFTALT, PRESS) == expected
    assert tracker.remove("one") is None
    assert tracker.handle("two", KEY_LEFTALT, RELEASE) == "ptt_stop"


def test_missing_dependency_has_nonretryable_exit(monkeypatch):
    monkeypatch.setattr(hotkeys, "dependency_ready", lambda: False)
    assert hotkeys.main(["--check"]) == 78


def test_dependency_check_never_listens(monkeypatch):
    monkeypatch.setattr(hotkeys, "dependency_ready", lambda: True)
    with patch.object(hotkeys, "run") as run:
        assert hotkeys.main(["--check"]) == 0
        run.assert_not_called()


def test_module_service_entrypoint_loads_local_auth_and_port(tmp_path, monkeypatch):
    source = tmp_path / "assistant" / "hotkeys.py"
    source.parent.mkdir()
    source.touch()
    (tmp_path / ".env").write_text(
        "THURSDAY_API_TOKEN=synthetic-service-token\nTHURSDAY_PORT=5505\n"
    )
    monkeypatch.setattr(hotkeys, "__file__", str(source))
    monkeypatch.delenv("THURSDAY_API_TOKEN", raising=False)
    monkeypatch.delenv("THURSDAY_PORT", raising=False)
    monkeypatch.setenv("THURSDAY_HOST", "127.0.0.1")
    monkeypatch.setattr(hotkeys, "dependency_ready", lambda: True)
    monkeypatch.setattr(hotkeys.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(hotkeys, "run", lambda _stop: VoiceClient().start())
    with patch.object(hotkeys.request, "urlopen", return_value=io.BytesIO(b"{}")) as opened:
        assert hotkeys.main([]) == 0
    req = opened.call_args.args[0]
    assert req.full_url == "http://127.0.0.1:5505/api/voice/start"
    assert req.get_header("Authorization") == "Bearer synthetic-service-token"


def test_voice_client_auth_and_session_specific_commands(monkeypatch):
    monkeypatch.setenv("THURSDAY_API_TOKEN", "synthetic-only")
    calls = []

    def reply(req, timeout):
        calls.append((req, timeout))
        return io.BytesIO(json.dumps({"session_id": "session-1", "state": "listening"}).encode())

    with patch.object(hotkeys.request, "urlopen", side_effect=reply):
        client = VoiceClient()
        client.start()
        client.stop()
        client.stop()
    assert len(calls) == 2
    assert calls[0][0].full_url.endswith("/api/voice/start")
    assert calls[0][0].get_header("Authorization") == "Bearer synthetic-only"
    assert json.loads(calls[1][0].data) == {"session_id": "session-1"}
    assert calls[1][0].full_url.endswith("/api/voice/stop")


def test_client_does_not_cancel_a_session_it_did_not_start():
    client = VoiceClient()
    with patch.object(client, "_post") as post:
        client.stop()
        client.cancel()
        post.assert_not_called()


def test_backend_error_body_is_not_logged(caplog):
    response = hotkeys.error.HTTPError(
        "http://127.0.0.1", 401, "Unauthorized", {}, io.BytesIO(b"secret-provider-data")
    )
    with patch.object(hotkeys.request, "urlopen", side_effect=response):
        VoiceClient().start()
    assert "HTTP 401" in caplog.text
    assert "secret-provider-data" not in caplog.text


def test_run_rediscovers_keyboard_and_cancels_on_disconnect(monkeypatch):
    stopped = threading.Event()
    actions = []

    def ev(code, value):
        return SimpleNamespace(type=hotkeys.EV_KEY, code=code, value=value)

    class Device:
        path = "synthetic-keyboard"
        closed = False

        def __init__(self, reads):
            self.reads = iter(reads)

        def read(self):
            result = next(self.reads)
            if isinstance(result, Exception):
                raise result
            return result

        def close(self):
            self.closed = True

    first = Device([[ev(KEY_LEFTMETA, PRESS), ev(KEY_LEFTALT, PRESS)], OSError()])
    second = Device([
        [ev(KEY_LEFTMETA, PRESS), ev(KEY_LEFTALT, PRESS)], [ev(KEY_LEFTALT, RELEASE)]
    ])
    available = iter([first, second])

    def discover(existing):
        if not existing:
            yield next(available)

    class Selector:
        device = None
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def register(self, dev, _events):
            self.device = dev

        def unregister(self, _dev):
            self.device = None

        def select(self, timeout):
            assert timeout <= 1
            self.calls += 1
            if self.calls > 4:
                stopped.set()
                return []
            return [(SimpleNamespace(fileobj=self.device), None)]

    class Client:
        def start(self):
            actions.append("start")

        def stop(self):
            actions.append("stop")

        def cancel(self):
            actions.append("cancel")

    ticks = iter(range(0, 20, 2))
    monkeypatch.setitem(hotkeys.sys.modules, "evdev", SimpleNamespace(
        ecodes=SimpleNamespace(EV_KEY=hotkeys.EV_KEY)
    ))
    monkeypatch.setattr(hotkeys.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(hotkeys, "find_keyboards", discover)
    monkeypatch.setattr(hotkeys.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(hotkeys, "VoiceClient", Client)
    hotkeys.run(stopped)
    assert actions == ["start", "cancel", "start", "stop", "cancel"]
    assert first.closed
    assert second.closed
