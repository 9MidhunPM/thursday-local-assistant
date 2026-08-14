from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import subprocess
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


class BrowserAutomationError(RuntimeError):
    """A user-facing browser automation failure."""


def _active_window() -> dict[str, Any]:
    if not shutil.which("hyprctl"):
        return {}
    result = subprocess.run(
        ["hyprctl", "activewindow", "-j"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def instagram_is_active() -> bool:
    active = _active_window()
    haystack = " ".join(
        str(active.get(key, "")) for key in ("class", "initialClass", "title")
    ).casefold()
    return "brave" in haystack and "instagram" in haystack


class BraveAutomationSession:
    """Own one visible persistent Brave context on a dedicated worker thread."""

    def __init__(self, profile_dir: Path | None = None) -> None:
        self.profile_dir = profile_dir or Path(
            os.getenv(
                "THURSDAY_BROWSER_PROFILE",
                "~/.local/share/thursday/browser-profile",
            )
        ).expanduser()
        self._requests: queue.Queue[
            tuple[Callable[[Any], Any] | None, Future[Any] | None]
        ] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker,
                name="thursday-brave-automation",
                daemon=True,
            )
            self._thread.start()

    def _worker(self) -> None:
        context = None
        playwright = None
        startup_error: Exception | None = None
        try:
            from playwright.sync_api import sync_playwright

            binary = shutil.which("brave") or shutil.which("brave-browser")
            if not binary:
                raise BrowserAutomationError("Brave is not installed.")
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                executable_path=binary,
                headless=False,
                no_viewport=True,
                ignore_default_args=["--enable-automation"],
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
        except Exception as exc:  # noqa: BLE001
            startup_error = exc

        while True:
            operation, future = self._requests.get()
            if operation is None:
                break
            if future is None or future.cancelled():
                continue
            if startup_error is not None:
                future.set_exception(
                    BrowserAutomationError(
                        "Thursday could not start its Brave automation profile: "
                        f"{startup_error}"
                    )
                )
                continue
            try:
                future.set_result(operation(context))
            except Exception as exc:  # noqa: BLE001
                future.set_exception(exc)

        if context is not None:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:  # noqa: BLE001
                pass

    def run(self, operation: Callable[[Any], T], timeout: float = 90) -> T:
        self._ensure_started()
        future: Future[T] = Future()
        self._requests.put((operation, future))
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            raise BrowserAutomationError(
                "Browser automation timed out. The page may still be loading."
            ) from exc

    def stop(self) -> None:
        thread = self._thread
        if not thread or not thread.is_alive():
            return
        self._requests.put((None, None))
        thread.join(timeout=10)


_SESSION = BraveAutomationSession()


def get_browser_automation() -> BraveAutomationSession:
    return _SESSION


def shutdown_browser_automation() -> None:
    _SESSION.stop()


atexit.register(shutdown_browser_automation)
