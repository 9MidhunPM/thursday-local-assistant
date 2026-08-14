from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BRAVE_CLASSES = ("brave-browser", "brave")
BRAVE_EXTENSION_DIR = Path(__file__).resolve().parents[1] / "browser_extension"


def _run(command: list[str], *, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _hyprland_clients() -> list[dict[str, Any]]:
    if not shutil.which("hyprctl"):
        return []
    result = _run(["hyprctl", "clients", "-j"], timeout=5)
    if result.returncode != 0:
        return []
    try:
        clients = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return clients if isinstance(clients, list) else []


def _is_brave_window(window: dict[str, Any]) -> bool:
    fields = (
        str(window.get("class", "")),
        str(window.get("initialClass", "")),
        str(window.get("title", "")),
    )
    haystack = " ".join(fields).casefold()
    return any(candidate in haystack for candidate in BRAVE_CLASSES)


@dataclass
class BraveController:
    """Open and safely type into the user's existing Brave Wayland session."""

    launch_timeout: float = 12

    @staticmethod
    def _binary() -> str | None:
        return shutil.which("brave") or shutil.which("brave-browser")

    def _brave_address(self, title_hint: str | None = None) -> str | None:
        clients = [c for c in _hyprland_clients() if c.get("mapped") and _is_brave_window(c)]
        if title_hint:
            matching = [
                client
                for client in clients
                if title_hint.casefold() in str(client.get("title", "")).casefold()
            ]
            clients = matching
        if not clients:
            return None
        # Prefer the compositor's most recently focused Brave window.
        clients.sort(key=lambda c: int(c.get("focusHistoryID", 2**31 - 1)))
        address = clients[0].get("address")
        return address if isinstance(address, str) and address else None

    def _active_address(self) -> str | None:
        if not shutil.which("hyprctl"):
            return None
        result = _run(["hyprctl", "activewindow", "-j"], timeout=5)
        if result.returncode != 0:
            return None
        try:
            active = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(active, dict) or not _is_brave_window(active):
            return None
        address = active.get("address")
        return address if isinstance(address, str) and address else None

    def focus(self, title_hint: str | None = None) -> tuple[bool, str | None]:
        address = self._brave_address(title_hint)
        if not address:
            return False, "Brave is running, but no mapped Brave window was found."
        result = _run(
            ["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
            timeout=5,
        )
        output = f"{result.stdout}\n{result.stderr}".casefold()
        if result.returncode != 0 or "error" in output:
            result = _run(
                [
                    "hyprctl",
                    "dispatch",
                    f'hl.dsp.focus({{ window = "address:{address}" }})',
                ],
                timeout=5,
            )
            output = f"{result.stdout}\n{result.stderr}".casefold()
            if result.returncode != 0 or "error" in output:
                return False, result.stderr.strip() or "Hyprland could not focus Brave."
        for _ in range(10):
            if self._active_address() == address:
                return True, None
            time.sleep(0.1)
        return False, "Brave did not become the active window; typing was cancelled."

    def open_url(self, url: str, title_hint: str | None = None) -> tuple[bool, str | None]:
        binary = self._binary()
        if not binary:
            return False, "Brave is not installed."
        if title_hint:
            focused, _ = self.focus(title_hint)
            if focused:
                return True, None
        launch_args = [binary]
        if BRAVE_EXTENSION_DIR.is_dir():
            launch_args.append(f"--load-extension={BRAVE_EXTENSION_DIR}")
        launch_args.append(url)
        subprocess.Popen(
            launch_args,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + self.launch_timeout
        last_error: str | None = None
        while time.monotonic() < deadline:
            focused, last_error = self.focus(title_hint)
            if focused:
                return True, None
            time.sleep(0.25)
        return False, last_error or "Brave did not open in time."

    def _send_key(self, key: str, modifiers: tuple[str, ...] = ()) -> tuple[bool, str | None]:
        if not shutil.which("wtype"):
            return False, "wtype is not installed."
        focused, error = self.focus()
        if not focused:
            return False, error
        args = ["wtype"]
        for modifier in modifiers:
            args.extend(["-M", modifier])
        args.extend(["-k", key])
        for modifier in reversed(modifiers):
            args.extend(["-m", modifier])
        result = _run(args, timeout=10)
        if result.returncode == 0:
            return True, None
        return False, result.stderr.strip() or f"Could not type key {key}."

    @staticmethod
    def _clipboard() -> tuple[bool, bytes]:
        if not shutil.which("wl-paste"):
            return False, b""
        result = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0, result.stdout

    @staticmethod
    def _copy(value: bytes) -> bool:
        if not shutil.which("wl-copy"):
            return False
        result = subprocess.run(
            ["wl-copy"],
            input=value,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return result.returncode == 0

    def _paste(self, text: str) -> tuple[bool, str | None]:
        if not self._copy(text.encode("utf-8")):
            return False, "wl-copy could not prepare text for browser input."
        time.sleep(0.05)
        return self._send_key("V", ("CTRL",))

    def fill_gmail_draft(
        self,
        recipient: str,
        subject: str,
        body: str,
    ) -> tuple[bool, str | None]:
        # Gmail's compose URL populates the fields itself. This avoids relying
        # on whichever element happened to own keyboard focus after the popup
        # rendered—a state wtype cannot inspect on Wayland.
        query = urllib.parse.urlencode(
            {
                "view": "cm",
                "fs": "1",
                "to": recipient,
                "su": subject,
                "body": body,
            },
            quote_via=urllib.parse.quote,
        )
        opened, error = self.open_url(
            f"https://mail.google.com/mail/u/0/?{query}",
            # Workspace/college accounts often brand the title as "Mail"
            # without the literal word "Gmail".
            title_hint="Mail",
        )
        if not opened:
            return False, error
        return True, None
