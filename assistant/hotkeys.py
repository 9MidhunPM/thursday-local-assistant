"""Super+Alt input forwarding for Thursday's shared backend voice controller.

Only this service reads evdev. Recording, transcription, cancellation and UI
state belong to the backend; no audio or transcripts are stored by this daemon.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import os
import selectors
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, request

from dotenv import load_dotenv

log = logging.getLogger("thursday.hotkeys")
EV_KEY = 0x01
KEY_LEFTALT = 56
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_C = 46
META_KEYS = frozenset({KEY_LEFTMETA, KEY_RIGHTMETA})
ALT_KEYS = frozenset({KEY_LEFTALT, KEY_RIGHTALT})
DEPENDENCY_EXIT = 78


def base_url() -> str:
    host = os.getenv("THURSDAY_HOST", "127.0.0.1")
    port = os.getenv("THURSDAY_PORT", "5005")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def dependency_ready() -> bool:
    try:
        importlib.import_module("evdev")
    except (ImportError, OSError):
        return False
    return True


@dataclass
class ComboTracker:
    """Pure combo detection for one keyboard; autorepeat never retriggers."""

    pressed: set[int] = field(default_factory=set)
    ptt_active: bool = False

    def handle(self, code: int, value: int) -> str | None:
        if value == 2:
            return None
        if value == 1:
            self.pressed.add(code)
        elif value == 0:
            self.pressed.discard(code)
        else:
            return None
        combo = bool(self.pressed & META_KEYS) and bool(self.pressed & ALT_KEYS)
        if combo and not self.ptt_active:
            self.ptt_active = True
            return "ptt_start"
        if not combo and self.ptt_active:
            self.ptt_active = False
            return "ptt_stop"
        return None


class KeyboardCombos:
    """Keep key state per device so one keyboard cannot release another's key."""

    def __init__(self) -> None:
        self.trackers: dict[str, ComboTracker] = {}

    @property
    def active(self) -> bool:
        return any(tracker.ptt_active for tracker in self.trackers.values())

    def handle(self, path: str, code: int, value: int) -> str | None:
        before = self.active
        self.trackers.setdefault(path, ComboTracker()).handle(code, value)
        after = self.active
        if after and not before:
            return "ptt_start"
        if before and not after:
            return "ptt_stop"
        return None

    def remove(self, path: str) -> str | None:
        before = self.active
        self.trackers.pop(path, None)
        return "ptt_cancel" if before and not self.active else None


class VoiceClient:
    """Forward authenticated session-specific controls to the backend."""

    def __init__(self) -> None:
        self.session_id: str | None = None

    def _post(self, action: str, payload: dict) -> dict | None:
        headers = {"Content-Type": "application/json"}
        token = os.getenv("THURSDAY_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = request.Request(
            f"{base_url()}/api/voice/{action}",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read())
            return result if isinstance(result, dict) else None
        except error.HTTPError as exc:
            # Never log response bodies: a local provider error may contain
            # credentials, transcript fragments, or private request context.
            log.warning("Voice %s rejected (HTTP %s)", action, exc.code)
        except Exception:
            log.warning("Voice %s unavailable; check Thursday's backend status", action)
        return None

    def start(self) -> None:
        result = self._post("start", {})
        self.session_id = result.get("session_id") if result else None
        if result and result.get("state") == "listening":
            log.info("PTT: listening")

    def stop(self) -> None:
        session_id, self.session_id = self.session_id, None
        if session_id is not None:
            self._post("stop", {"session_id": session_id})
            log.info("PTT: released")

    def cancel(self) -> None:
        session_id, self.session_id = self.session_id, None
        if session_id is not None:
            self._post("cancel", {"session_id": session_id})


def find_keyboards(existing: set[str] | None = None):
    import evdev  # noqa: PLC0415 - optional dependency checked by main

    for path in evdev.list_devices():
        if existing and path in existing:
            continue
        dev = None
        try:
            dev = evdev.InputDevice(path)
            keys = set(dev.capabilities().get(EV_KEY, []))
            if keys & META_KEYS and keys & ALT_KEYS and KEY_C in keys:
                yield dev
            else:
                dev.close()
        except Exception:
            if dev is not None:
                with contextlib.suppress(OSError):
                    dev.close()


def run(stop_event: threading.Event | None = None) -> None:
    import evdev  # noqa: PLC0415 - optional dependency checked by main

    stopped = stop_event or threading.Event()
    devices: dict[str, object] = {}
    tracker = KeyboardCombos()
    client = VoiceClient()
    next_discovery = 0.0
    warned_no_devices = False
    with selectors.DefaultSelector() as selector:
        try:
            while not stopped.is_set():
                now = time.monotonic()
                if now >= next_discovery:
                    next_discovery = now + 1
                    for dev in find_keyboards(set(devices)):
                        try:
                            selector.register(dev, selectors.EVENT_READ)
                        except Exception:
                            dev.close()
                            continue
                        devices[dev.path] = dev
                        log.info("Keyboard connected; hold Super+Alt to talk")
                    if not devices and not warned_no_devices:
                        log.warning("No readable keyboards; check input group access or reconnect")
                    warned_no_devices = not bool(devices)
                for key, _ in selector.select(timeout=0.25):
                    dev = key.fileobj
                    try:
                        events = dev.read()
                    except BlockingIOError:
                        continue
                    except OSError:
                        selector.unregister(dev)
                        devices.pop(dev.path, None)
                        if tracker.remove(dev.path) == "ptt_cancel":
                            client.cancel()
                        dev.close()
                        log.info("Keyboard disconnected; waiting for reconnect")
                        continue
                    for event in events:
                        if event.type != evdev.ecodes.EV_KEY:
                            continue
                        action = tracker.handle(dev.path, event.code, event.value)
                        if action == "ptt_start":
                            client.start()
                        elif action == "ptt_stop":
                            client.stop()
        finally:
            client.cancel()
            for dev in devices.values():
                dev.close()


def main(argv: list[str] | None = None) -> int:
    # The supervised service invokes this module directly. Load the same local
    # configuration as the backend without embedding credentials in its unit.
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check input dependencies without listening"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, os.getenv("THURSDAY_HOTKEYS_LOG", "INFO").upper(), logging.INFO),
        format="[thursday-hotkeys] %(levelname)s %(message)s",
    )
    if not dependency_ready():
        log.error("Missing evdev. Install Thursday's hotkeys extra with the service interpreter.")
        return DEPENDENCY_EXIT
    if args.check:
        return 0
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: stopped.set())
    log.info("Hotkey daemon ready — hold Super+Alt to talk")
    run(stopped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
