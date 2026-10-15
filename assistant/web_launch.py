"""Open the detailed web app through one supervised backend and a dedicated window."""

from __future__ import annotations

# Fixed diagnostics intentionally omit subprocess output and private configuration.
# ruff: noqa: TRY003
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

from assistant.paths import private_dir, private_write, runtime_dir

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WebLaunchError(OSError):
    """A stable, safe message suitable for native or terminal display."""


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or any(ord(character) < 32 for character in url):
        raise WebLaunchError("The web app requires a local address without credentials.")
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in {"", "/"}
        )
        port = parsed.port
        valid = valid and (port is None or 1 <= port <= 65535)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise WebLaunchError("The web app requires a local address without credentials.")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return "http://" + host + (f":{port}" if port not in {None, 80} else "")


def _app_identity(url: str) -> tuple[str, set[str]]:
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port or 80
    app_class = "thursday-web-" + hashlib.sha256(url.encode()).hexdigest()[:12]
    # Chromium's app-mode class contains host/path, but omits the origin's port.
    # A separate SPA path keeps the identity distinct for each local backend.
    return app_class, {app_class, f"brave-{parsed.hostname}__desktop_{port}-Default"}


def _has_hyprland() -> bool:
    return bool(os.getenv("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"))


@contextmanager
def _launch_guard(url: str):
    directory = runtime_dir("web-launch")
    name = hashlib.sha256(url.encode()).hexdigest()[:24]
    descriptor = os.open(
        directory / (name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        deadline = time.monotonic() + 12
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WebLaunchError(
                        "The web app is already starting. Please wait a moment."
                    ) from None
                time.sleep(0.05)
        yield directory / (name + ".pending")
    finally:
        os.close(descriptor)


def _wait_for_web(url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            with opener.open(url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.15)
    raise WebLaunchError("Thursday's web server did not start. Check thursday.service.")


def _focus_window(address: str) -> None:
    try:
        from hypruse.hyprctl import HyprctlError, dispatch  # noqa: PLC0415
    except ImportError:
        subprocess.run(
            ["hyprctl", "dispatch", "focuswindow", "address:" + address],
            capture_output=True,
            timeout=2,
            check=True,
        )
    else:
        try:
            dispatch("focuswindow", "address:" + address)
        except HyprctlError:
            raise WebLaunchError("The existing web window could not be focused.") from None


def _focus_existing(url: str) -> bool:
    if not shutil.which("hyprctl"):
        return False
    try:
        result = subprocess.run(
            ["hyprctl", "-j", "clients"], capture_output=True, text=True, timeout=2, check=True
        )
        clients = json.loads(result.stdout)
        for client in clients if isinstance(clients, list) else []:
            address = client.get("address", "")
            _app_class, classes = _app_identity(url)
            if client.get("class") in classes and re.fullmatch(r"0x[0-9a-fA-F]+", address):
                # Chromium app windows use a URL-derived class despite --class.
                try:
                    _focus_window(address)
                except (OSError, subprocess.SubprocessError):
                    raise WebLaunchError("The existing web window could not be focused.") from None
                return True
    except WebLaunchError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError, TypeError):
        if _has_hyprland():
            raise WebLaunchError(
                "The desktop connection is unavailable. Check Hyprland before reopening "
                "the web app."
            ) from None
    return False


def open_web_app(base_url: str, *, start_service: bool = False) -> dict:
    """Dispatch one local app window, reusing the existing dedicated window when possible."""
    url = _validate_url(base_url)
    try:
        if start_service:
            subprocess.run(
                ["systemctl", "--user", "start", "thursday.service"],
                capture_output=True,
                timeout=40,
                check=True,
            )
        _wait_for_web(url)
        with _launch_guard(url) as pending:
            if _focus_existing(url):
                pending.unlink(missing_ok=True)
                return {"status": "launch_requested", "reused": True}
            try:
                age = time.monotonic() - float(pending.read_text())
            except (OSError, ValueError):
                age = 16
            if 0 <= age < 15:
                raise WebLaunchError(
                    "A web window was already requested. Wait for it to appear before retrying."
                )
            brave = shutil.which("brave") or shutil.which("brave-browser")
            if brave:
                config = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config")))
                profile = private_dir(config / "brave-thursday-app")
                app_class, _classes = _app_identity(url)
                app_url = url + "/desktop/" + str(urllib.parse.urlsplit(url).port or 80)
                process = subprocess.Popen(
                    [
                        brave,
                        "--class=" + app_class,
                        f"--app={app_url}",
                        f"--user-data-dir={profile}",
                    ],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                private_write(pending, str(time.monotonic()))
                if _has_hyprland():
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        if _focus_existing(url):
                            pending.unlink(missing_ok=True)
                            break
                        if process.poll() not in {None, 0}:
                            pending.unlink(missing_ok=True)
                            raise WebLaunchError(
                                "The web browser could not start. Check its configuration."
                            )
                        time.sleep(0.1)
                    else:
                        raise WebLaunchError(
                            "The web window was requested but is not visible yet. "
                            "Please wait before retrying."
                        )
            else:
                subprocess.run(["xdg-open", url], capture_output=True, timeout=8, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        if isinstance(exc, WebLaunchError):
            raise
        raise WebLaunchError(
            "The web app couldn't open. Check your browser and thursday.service."
        ) from None
    return {"status": "launch_requested", "reused": False}


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    host = os.getenv("THURSDAY_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = os.getenv("THURSDAY_BASE_URL", f"http://{host}:{os.getenv('THURSDAY_PORT', '5005')}")
    try:
        print(json.dumps(open_web_app(url, start_service=True)))
    except WebLaunchError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
