from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from assistant.tools.base import BaseTool, ToolMetadata

if TYPE_CHECKING:
    from assistant.agent.context import ExecutionContext

ADDRESS = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)


def _run(*command: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(list(command), capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _output(*command: str) -> str | None:
    result = _run(*command)
    return result.stdout.strip() if result and result.returncode == 0 else None


def _hyprland_json(query: str) -> Any:
    try:
        return json.loads(_output("hyprctl", query, "-j") or "null")
    except ValueError:
        return None


@dataclass(frozen=True)
class _Window:
    address: str
    title: str
    app_class: str
    workspace: int | None
    pid: int | None

    @classmethod
    def from_hyprland(cls, item: dict[str, Any]) -> _Window:
        workspace = item.get("workspace")
        return cls(
            item["address"],
            str(item.get("title", "")),
            str(item.get("class", "")),
            workspace.get("id") if isinstance(workspace, dict) else None,
            item.get("pid"),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "title": self.title,
            "class": self.app_class,
            "workspace": self.workspace,
            "pid": self.pid,
        }


def _hyprland_windows() -> list[_Window] | None:
    clients = _hyprland_json("clients")
    if not isinstance(clients, list):
        return None
    return [
        _Window.from_hyprland(client)
        for client in clients
        if isinstance(client, dict)
        and client.get("mapped")
        and ADDRESS.fullmatch(str(client.get("address", "")))
    ]


def _x11_windows() -> list[_Window] | None:
    listing = _output("wmctrl", "-lp")
    if listing is None:
        return None
    windows = []
    for line in listing.splitlines():
        parts = line.split(maxsplit=4)
        if len(parts) < 4 or not ADDRESS.fullmatch(parts[0]):
            continue
        address, workspace, pid, _host = parts[:4]
        try:
            windows.append(
                _Window(address, parts[4] if len(parts) == 5 else "", "", int(workspace), int(pid))
            )
        except ValueError:
            continue
    return windows


def _active_workspace(hyprland: bool) -> int | None:
    if hyprland:
        workspace = _hyprland_json("activeworkspace")
        return workspace.get("id") if isinstance(workspace, dict) else None
    listing = _output("wmctrl", "-d")
    for line in (listing or "").splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2 and parts[1] == "*" and parts[0].isdecimal():
            return int(parts[0])
    return None


def _matching_windows(windows: list[_Window], query: str) -> list[_Window]:
    query = query.casefold()
    if ADDRESS.fullmatch(query):
        return [window for window in windows if int(window.address, 16) == int(query, 16)]
    exact, partial = [], []
    for window in windows:
        names = [window.title.casefold(), window.app_class.casefold()]
        if query in names:
            exact.append(window)
        elif any(query in name for name in names):
            partial.append(window)
    return exact or partial


def _dispatch_focus(target: _Window, hyprland: bool) -> bool:
    if not hyprland:
        return shutil.which("xprop") is not None and _output(
            "wmctrl", "-ia", target.address
        ) is not None
    address = f"address:{target.address}"
    result = _run("hyprctl", "dispatch", "focuswindow", address)
    if result is None:
        return False
    if _dispatch_rejected(result):
        result = _run("hyprctl", "dispatch", f'hl.dsp.focus({{ window = "{address}" }})')
    return result is not None and not _dispatch_rejected(result)


def _dispatch_rejected(result: subprocess.CompletedProcess[str]) -> bool:
    output = f"{result.stdout}\n{result.stderr}".casefold()
    return result.returncode != 0 or any(word in output for word in ("error", "invalid"))


def _focus_verified(target: _Window, hyprland: bool) -> bool:
    for attempt in range(5):
        if hyprland:
            active = _hyprland_json("activewindow")
            if (
                isinstance(active, dict)
                and active.get("address") == target.address
                and (target.pid is None or active.get("pid") == target.pid)
                and (not target.app_class or active.get("class") == target.app_class)
            ):
                return True
        else:
            active = _output("xprop", "-root", "_NET_ACTIVE_WINDOW")
            match = ADDRESS.search(active or "")
            if match and int(match.group(), 16) == int(target.address, 16):
                return True
        if attempt < 4:
            time.sleep(0.05)
    return False


class WindowManagementTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="window_management",
        description="List open windows or focus a unique title, app name, or observed address.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "focus"]},
                "window_name": {
                    "type": "string",
                    "description": "Title, app name, or observed address. Required for focus.",
                },
            },
            "required": ["action"],
        },
        resource="desktop",
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        if action not in {"list", "focus"}:
            return {"success": False, "error": f"Unknown action: {action}"}
        query = str(arguments.get("window_name") or "").strip()
        if action == "focus" and not query:
            return {"success": False, "error": "window_name is required for focus action."}

        windows = _hyprland_windows() if shutil.which("hyprctl") else None
        hyprland = windows is not None
        if windows is None and not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            windows = _x11_windows() if shutil.which("wmctrl") else None
        if windows is None:
            return {"success": False, "error": "Could not read windows from the active desktop."}
        if action == "list":
            labels = [
                f"{item.app_class} - {item.title}" if hyprland else item.title
                for item in windows
                if item.app_class or item.title
            ]
            return {
                "success": True,
                "output": "Open windows:\n" + "\n".join(labels),
                "windows": [window.summary() for window in windows],
            }

        candidates = _matching_windows(windows, query)
        if len(candidates) > 1:
            workspace = _active_workspace(hyprland)
            if workspace is not None:
                nearby = [window for window in candidates if window.workspace == workspace]
                if len(nearby) == 1:
                    candidates = nearby
        if not candidates:
            return {"success": False, "error": f"No open window matches '{query}'."}
        if len(candidates) > 1:
            return {
                "success": False,
                "error_code": "ambiguous_window",
                "error": "Several windows match. Choose one by its observed address.",
                "candidates": [window.summary() for window in candidates],
            }

        task = getattr(context, "task", None)
        if task:
            task.check()
        target = candidates[0]
        if not _dispatch_focus(target, hyprland) or not _focus_verified(target, hyprland):
            return {
                "success": False,
                "error": "Could not verify window focus. Observe the desktop again before typing.",
            }
        return {
            "success": True,
            "output": f"Focused {target.title or target.app_class or target.address}.",
            "window": target.summary(),
            "backend": "hyprland" if hyprland else "x11",
            "verification": "The selected window is active.",
        }


def get_tools() -> list[BaseTool]:
    return [WindowManagementTool()]
