from __future__ import annotations

from assistant.tools.reels_tool import ReelsWatcher


class FakeController:
    def __init__(self) -> None:
        self.calls = 0

    def open_url(self, url, title_hint=None):
        self.calls += 1
        return True, None


class FakeBridge:
    def __init__(self) -> None:
        self.connected = False
        self.running = False

    def status(self):
        return {"connected": self.connected}

    def wait_until_connected(self, timeout=20):
        self.connected = True
        return True

    def request(self, action, payload, timeout=45):
        if action == "instagram.reels.start":
            already_running = self.running
            self.running = True
            return {"running": True, "already_running": already_running}
        stopped = self.running
        self.running = False
        return {"stopped": stopped}


def test_reels_start_is_idempotent_and_stop_is_safe() -> None:
    controller = FakeController()
    watcher = ReelsWatcher(  # type: ignore[arg-type]
        controller=controller, bridge=FakeBridge(), interval_seconds=60
    )
    started, error, already_running = watcher.start()
    assert started and error is None and not already_running
    started, error, already_running = watcher.start()
    assert started and error is None and already_running
    assert controller.calls == 1
    assert watcher.stop()
    assert not watcher.stop()


def test_reels_open_failure_does_not_start_watcher() -> None:
    class FailedController(FakeController):
        def open_url(self, url, title_hint=None):
            return False, "open failed"

    watcher = ReelsWatcher(  # type: ignore[arg-type]
        controller=FailedController(), bridge=FakeBridge(), interval_seconds=60
    )
    started, error, already_running = watcher.start()
    assert not started and not already_running
    assert error == "open failed"
    assert not watcher.running
