"""Effect-based desktop approval policy, using observed controls where available."""

from __future__ import annotations

import re
import time

CONSEQUENTIAL = re.compile(
    r"\b(close|quit|exit|kill|send|submit|publish|post|purchase|buy|pay|delete|remove|"
    r"trash|checkout|overwrite|replace|discard|install|uninstall|permission|password|"
    r"security|authorize|confirm order)\b|"
    r"^(ok|okay|yes|confirm|apply|allow|accept|x|\u00d7|\u2715)$|"
    r"^\s*save(?:\s+as|\s+all|\s+file|\s+changes)?(?:\s*[.…]+)?\s*$",
    re.IGNORECASE,
)


def key_parts(keys):
    aliases = {
        "control": "ctrl",
        "mod4": "super",
        "win": "super",
        "return": "enter",
        "del": "delete",
    }
    return frozenset(aliases.get(p, p) for p in re.split(r"[+\s]+", str(keys).lower()) if p)


def records(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from records(child)
    elif isinstance(value, list):
        for child in value:
            yield from records(child)


def evidence(bridge, window=None):
    cached = getattr(bridge, "policy_evidence", {})
    if not isinstance(cached, dict):
        return []
    item = cached.get(window or (bridge.snapshot or {}).get("active_window"))
    if not item or time.monotonic() - item[0] > 5:
        return []
    return list(records(item[1]))


def control_name(item):
    return str(item.get("name") or item.get("label") or "")


def at_point(item, x, y):
    rect = item.get("rect") or item.get("extent") or item.get("bounds") or item.get("geometry")
    if isinstance(rect, dict):
        rect = [rect.get(k) for k in ("x", "y", "width", "height")]
    if isinstance(rect, (tuple, list)) and len(rect) == 4:
        try:
            a, b, w, h = map(float, rect)
            return a <= x < a + w and b <= y < b + h
        except (TypeError, ValueError):
            return False
    point = item.get("click") or item.get("center") or item.get("at")
    if isinstance(point, (tuple, list)) and len(point) == 2:
        return point == [x, y] or point == (x, y)
    return item.get("x") == x and item.get("y") == y


def binding_approval(combo, bridge):
    """Resolve every matching binding, including duplicate bindings for one combo."""
    cached = getattr(bridge, "policy_bindings", None)
    if not cached:
        return None
    matches = [
        item
        for item in records(cached[1])
        if item.get("combo") and key_parts(item["combo"]) == key_parts(combo)
    ]
    if not matches:
        return None
    if time.monotonic() - cached[0] > 30:
        return True
    safe = {
        "workspace",
        "movetoworkspace",
        "movetoworkspacesilent",
        "focuswindow",
        "movefocus",
        "fullscreen",
        "togglefloating",
        "resizeactive",
        "moveactive",
    }
    return any(
        str(item.get("dispatcher") or item.get("action") or "") not in safe for item in matches
    )


def needs_approval(name, args, bridge):
    """Unknown activation effects remain gated; navigation never is."""
    action = args.get("action", "")
    if name == "hypr":
        return action in {"close_window", "kill", "killactive"}
    if name == "use_bind":
        return binding_approval(args.get("combo"), bridge) is not False
    if name == "click_ui":
        if args.get("mark") is not None:
            controls = [
                r
                for r in evidence(bridge, args.get("window"))
                if str(r.get("mark")) == str(args["mark"])
            ]
            return not controls or any(
                not control_name(r) or CONSEQUENTIAL.search(control_name(r)) for r in controls
            )
        label = str(args.get("name") or "")
        return not label or bool(CONSEQUENTIAL.search(label))
    if name == "pointer":
        if action in {"move", "scroll"}:
            return False
        if action == "click":
            if args.get("button", "left") == "right":
                return False
            if args.get("button", "left") != "left":
                return True
            controls = [
                r
                for r in evidence(bridge)
                if control_name(r) and at_point(r, args.get("x"), args.get("y"))
            ]
            return not controls or any(CONSEQUENTIAL.search(control_name(r)) for r in controls)
        return True  # Unknown drag/drop may move data to trash or overwrite.
    if name == "keyboard" and action == "key":
        keys = key_parts(args.get("keys"))
        if (
            {"ctrl", "w"} <= keys
            or {"ctrl", "f4"} <= keys
            or {"alt", "f4"} <= keys
            or {"super", "q"} <= keys
            or {"ctrl", "q"} <= keys
            or {"ctrl", "s"} <= keys
        ):
            return True
        if binding_approval(args.get("keys"), bridge) is True:
            return True
        if "enter" in keys or "delete" in keys:
            provenance = getattr(bridge, "policy_input", None)
            if (
                keys == {"enter"}
                and provenance
                and provenance[1] == args.get("window")
                and time.monotonic() - provenance[0] < 5
            ):
                return False
            controls = [
                r
                for r in evidence(bridge, args.get("window"))
                if r.get("focused") is True or "focused" in r.get("states", [])
            ]
            if not controls:
                return True
            if "enter" in keys:
                return not all(
                    re.search(
                        r"search|address|location|url",
                        control_name(r) + " " + str(r.get("role", "")),
                        re.IGNORECASE,
                    )
                    and not CONSEQUENTIAL.search(control_name(r))
                    for r in controls
                )
            return not all(
                str(r.get("role", "")).lower()
                in {"text", "entry", "text box", "textbox", "search box"}
                for r in controls
            )
    return False


def action_label(tool, arguments):
    """Public activity labels deliberately omit typed text and private result data."""
    operation = tool.removeprefix("hypruse__")
    action = arguments.get("action", "")
    if operation == "click_ui":
        label = str(arguments.get("name", ""))
        if re.fullmatch(
            r"(search|save|open|downloads|next|back|play|pause|close|send|cancel)"
            r"( tab| window| file| message)?",
            label,
            re.IGNORECASE,
        ):
            return "Selecting " + label
    labels = {
        "desktop": "Checking the desktop",
        "ui": "Reading window controls",
        "marks": "Locating controls",
        "screenshot": "Inspecting the screen",
        "zoom": "Inspecting a screen detail",
        "binds": "Checking shortcuts",
        "wait_for": "Waiting for the desktop",
        "launch": "Opening an application",
        "click_ui": "Selecting a control",
        "sequence": "Performing desktop actions",
        "sumi_open": "Opening a Sumi view",
        "use_bind": "Using a desktop shortcut",
    }
    if operation == "pointer":
        return {
            "move": "Moving the pointer",
            "scroll": "Scrolling",
            "click": "Clicking a control",
            "drag": "Dragging",
        }.get(action, "Using the pointer")
    if operation == "keyboard":
        return "Typing into the selected window" if action == "type" else "Using the keyboard"
    if operation == "hypr":
        return {
            "focus_window": "Focusing a window",
            "close_window": "Closing a window",
            "workspace": "Switching workspace",
        }.get(action, "Arranging windows")
    return labels.get(operation, "Using " + tool.replace("_", " "))
