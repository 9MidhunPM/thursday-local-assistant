"""Small, task-scoped references to observed desktop and file targets."""

import re

_DESKTOP_REQUEST = re.compile(
    r"\b(?:window|workspace|screen|desktop|brave|firefox|chromium|browser tab|"
    r"(?:this|current|active) tab|editor|vs ?code|visual studio code|"
    r"thunar|kate|mousepad|sumi|bluetooth|wi-?fi|file finder|show my files)\b",
    re.IGNORECASE,
)
_DESKTOP_ACTION = re.compile(
    r"\b(?:show|open|focus|switch|arrange|move|launch|click|scroll|type|paste|read|"
    r"look|see|inspect|copy|take|grab|close|find|add|put|go|check|use|select|"
    r"bring|summari[sz]e|tell|what(?:'?s| is)|navigate)\b",
    re.IGNORECASE,
)
_DESKTOP_INFORMATION = re.compile(
    r"^(?:what(?:'s| is) a[n]?\b|what (?:are|is) (?!"
    r"(?:on |in )?(?:my |this |the (?:active|current|visible) ))|"
    r"how (?:do|can|should|does|to)\b|(?:explain|define)\b)",
    re.IGNORECASE,
)
_FOLLOW_UP = re.compile(
    r"\b(?:it|them|there|here|this|that|those|other|same|first|second|third|"
    r"last|next|previous|again|retry)\b",
    re.IGNORECASE,
)
_NUMBERED_CHOICE = re.compile(r"\s*(?:option\s+)?[1-9][0-9]?\s*[.!]?\s*", re.IGNORECASE)
_SHORT_FOLLOW_UP = re.compile(r"^(?:the|retry|again)\b", re.IGNORECASE)
_FILE_TOOLS = {"find_file_system", "search_files", "find_folders"}
_WINDOW_TOOLS = {"hypruse__ui", "hypruse__marks", "hypruse__screenshot", "hypruse__zoom"}
_NAVIGATION_TOOLS = {
    "open_app", "open_path", "open_url", "reveal_path", "window_management",
    "open_google_search", "youtube_play", "sumi_open",
}


def desktop_request(text):
    return bool(
        not _DESKTOP_INFORMATION.match(text.lstrip())
        and _DESKTOP_ACTION.search(text)
        and _DESKTOP_REQUEST.search(text)
    )


def reference_follow_up(text):
    return bool(
        numbered_choice(text)
        or (
            len(text.split()) <= 16
            and _FOLLOW_UP.search(text)
            and (_DESKTOP_ACTION.search(text) or _SHORT_FOLLOW_UP.match(text))
        )
    )


def numbered_choice(text):
    return bool(_NUMBERED_CHOICE.fullmatch(text))


def desktop_tool(name):
    return name.startswith("hypruse__") or name in {"window_management", "sumi_open"}


def changes_view(tool, arguments):
    if tool == "window_management":
        return arguments.get("action") != "list"
    if tool == "hypruse__pointer":
        return arguments.get("action") != "move"
    return tool in _NAVIGATION_TOOLS or tool in {
        "hypruse__click_ui", "hypruse__hypr", "hypruse__keyboard", "hypruse__launch",
        "hypruse__sequence", "hypruse__use_bind", "hypruse__wait_for",
    }


def replaces_capture(tool, arguments):
    return changes_view(tool, arguments) or tool in {
        "hypruse__desktop", "hypruse__ui", "hypruse__marks",
        "hypruse__screenshot", "hypruse__zoom",
    }


def workspace_view(snapshot):
    if not snapshot:
        return {}
    active = snapshot.get("active_window")
    windows = [window for window in snapshot.get("windows", []) if not window.get("hidden")]
    focused = next((window for window in windows if window.get("address") == active), {})
    monitor = next((item for item in snapshot.get("monitors", []) if item.get("focused")), {})
    workspace = focused.get("workspace")
    if workspace is None or not str(workspace).startswith("-"):
        workspace = monitor.get("active_workspace", workspace)
    visible = [
        window for window in windows
        if window.get("workspace") == workspace or window.get("address") == active
    ]
    visible = [window for window in visible if not is_thursday_window(window)]
    visible.sort(key=lambda window: window.get("address") != active)
    return {
        "workspace": workspace,
        "monitor": monitor.get("name"),
        "active_window": active if not is_thursday_window(focused) else None,
        "windows": [window_ref(window) for window in visible[:20]],
    }


def is_thursday_window(window):
    app = str(window.get("class") or "").casefold()
    title = str(window.get("title") or "").casefold()
    return (
        app.startswith("thursday-web-")
        or app.startswith("brave-127.0.0.1__desktop_")
        or title == "thursday - ai assistant"
    )


def window_ref(window):
    return {
        key: window.get(key)
        for key in ("address", "class", "pid", "workspace")
    } | {"title": str(window.get("title") or "")[:160]}


def live_references(references, snapshot):
    windows = {
        window.get("address"): window
        for window in (snapshot or {}).get("windows", [])
        if not window.get("hidden")
    }
    return [
        window_ref(current)
        for previous in references
        if (current := windows.get(previous.get("address")))
        and all(current.get(key) == previous.get(key) for key in ("class", "pid"))
    ]


def update_references(references, result, arguments, snapshot):
    name = result["tool"]
    if name == "window_management":
        matches = result.get("candidates", result.get("windows"))
        if isinstance(matches, list):
            references["window_matches"] = [window_ref(window) for window in matches[:12]]
    if not result.get("success"):
        return
    output = result.get("output")
    if name in _FILE_TOOLS:
        matches = output.get("results") if isinstance(output, dict) else output
        if isinstance(matches, list):
            file_type = "folder" if name == "find_folders" else "file"
            files = []
            for index, item in enumerate(matches[:12], 1):
                if isinstance(item, str):
                    files.append({"index": index, "type": file_type, "path": item})
                elif isinstance(item, dict) and isinstance(item.get("path"), str):
                    files.append({key: item.get(key) for key in ("index", "type", "path")})
            references["files"] = files
            references.pop("selected_file", None)
        return
    if name in {"open_path", "reveal_path"} and isinstance(arguments.get("path"), str):
        references["selected_file"] = result.get("path") or arguments["path"]
    selected = result.get("window") if name == "window_management" else None
    if name in _WINDOW_TOOLS | {"hypruse__keyboard", "hypruse__click_ui", "hypruse__hypr"}:
        address = arguments.get("window") or arguments.get("target")
        if not address and name in _WINDOW_TOOLS:
            address = (snapshot or {}).get("active_window")
        selected = next(
            (window for window in (snapshot or {}).get("windows", [])
             if address and window.get("address") == address), None,
        )
    if selected:
        address = selected["address"]
        earlier = [item for item in references.get("windows", []) if item["address"] != address]
        references["windows"] = [*earlier, window_ref(selected)][-6:]
