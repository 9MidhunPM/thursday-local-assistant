"""Open a specific view in the running Sumi desktop."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

from assistant.tools.base import BaseTool, ToolMetadata

if TYPE_CHECKING:
    from assistant.agent.context import ExecutionContext


ROUTES = (
    "audio", "bluetooth", "calendar", "clipboard", "codex", "controls", "files",
    "help", "inbox", "launcher", "media", "wifi",
)


def _run(executable: str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _decode(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _ipc(executable: str, instance: str, target: str, method: str, *args: str) -> str | None:
    return _run(executable, "ipc", "--id", instance, "call", target, method, *args)


def _sumi_instance(executable: str) -> tuple[str, dict[str, Any]] | None:
    # Quickshell can run standalone and preview shells alongside Sumi. Probe
    # only their read-only status method, then pin all calls to one current ID.
    instances = _decode(_run(executable, "list", "--all", "--json"))
    if not isinstance(instances, list):
        return None
    matches = []
    for instance in instances:
        instance_id = instance.get("id") if isinstance(instance, dict) else None
        if not isinstance(instance_id, str) or not re.fullmatch(r"[a-zA-Z0-9]+", instance_id):
            continue
        status = _decode(_ipc(executable, instance_id, "sumi", "status"))
        if isinstance(status, dict) and status.get("preview") is False:
            matches.append((instance_id, status))
    return matches[0] if len(matches) == 1 else None


def _visible_view(executable: str, instance_id: str) -> dict[str, Any] | None:
    views = _decode(_ipc(executable, instance_id, "island", "status"))
    if not isinstance(views, list):
        return None
    visible = [
        view for view in views
        if isinstance(view, dict)
        and view.get("visible") is True
        and view.get("pinned") is True
        and isinstance(view.get("section"), str)
    ]
    return visible[0] if len(visible) == 1 else None


def visible_sumi_route() -> str | None:
    """Return the pinned, visible route for the sole non-preview Sumi instance."""
    executable = shutil.which("qs")
    if not executable:
        return None
    instance = _sumi_instance(executable)
    view = _visible_view(executable, instance[0]) if instance else None
    return view["section"] if view else None


class SumiOpenTool(BaseTool):
    metadata = ToolMetadata(
        name="sumi_open",
        description=(
            "Open a built-in Sumi island view on the focused screen. Use for requests to show "
            "the desktop file finder, Bluetooth devices, Wi-Fi, or another named Sumi view. "
            "Only opens the view. File search and settings changes require separate tools."
        ),
        parameters={
            "type": "object",
            "properties": {"route": {"type": "string", "enum": list(ROUTES)}},
            "required": ["route"],
            "additionalProperties": False,
        },
        effect="desktop",
        resource="desktop",
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        route = arguments.get("route")
        if route not in ROUTES:
            return {"success": False, "error": "Choose an available Sumi view."}
        executable = shutil.which("qs")
        instance = _sumi_instance(executable) if executable else None
        if not executable or not instance:
            return {
                "success": False, "delivery": "not_delivered",
                "error": "Thursday could not identify one running Sumi desktop on this display.",
            }
        instance_id, status = instance
        if status.get("nativeDialogOpen") or status.get("captureSelecting"):
            return {
                "success": False, "delivery": "not_delivered",
                "error": "Finish the open Sumi dialog or capture selection first.",
            }
        task = context.task
        if task:
            task.check()
        delivery = _ipc(executable, instance_id, "island", "open", route)
        view = _visible_view(executable, instance_id)
        if view and view["section"] == route:
            return {
                "success": True,
                "output": {"route": route, "screen": view.get("screen")},
                "verification": "Observed the requested Sumi view open.",
            }
        return {
            "success": False, "delivery": "uncertain_delivery", "pause": True,
            "error": (
                "Sumi did not acknowledge or show the requested view." if delivery is None
                else "Sumi acknowledged the request but the view could not be verified."
            ),
        }


def get_tools() -> list[BaseTool]:
    return [SumiOpenTool()]
