from __future__ import annotations

"""Tool grouping + smart filtering to keep context windows lean and models focused."""

# Map tool name -> group. Tools not listed are "misc" (always available unless filtered).
TOOL_GROUPS: dict[str, str] = {
    # Core
    "current_time": "core",
    "current_date": "core",
    # Memory
    "store_preference": "memory",
    "get_preference": "memory",
    "delete_preference": "memory",
    "store_memory": "memory",
    "recall_memory": "memory",
    "delete_memory": "memory",
    "store_fact": "memory",
    "search_personal_knowledge": "memory",
    "get_entity_profile": "memory",
    "delete_fact": "memory",
    "delete_entity_facts": "memory",
    "forget_everything": "memory",
    "list_all_memory": "memory",
    # Files
    "read_file": "files",
    "write_file": "files",
    "search_files": "files",
    "find_folders": "files",
    "find_file_system": "files",
    "open_path": "files",
    "reveal_path": "files",
    "move_file": "files",
    "delete_file": "files",
    # Apps
    "open_app": "apps",
    "search_apps": "apps",
    # Terminal
    "run_terminal_command": "terminal",
    # Codex project workspace
    "codex_orchestrate": "codex",
    # Web
    "web_search": "web",
    "fetch_page": "web",
    "search_and_fetch": "web",
    "open_google_search": "web",
    "analyze_website": "web",
    "news": "web",
    "youtube_search": "web",
    "youtube_play": "web",
    "translate_text": "web",
    "define_word": "web",
    # Media
    "spotify_search_play": "media",
    "spotify_control": "media",
    "spotify_library": "media",
    "spotify_play_playlist": "media",
    "volume_control": "media",
    # Desktop
    "brightness_control": "desktop",
    "clipboard": "desktop",
    "window_management": "desktop",
    "take_screenshot": "desktop",
    "send_notification": "desktop",
    # System
    "system_status": "system",
    "system_monitor": "system",
    "kill_process": "system",
    # Utility
    "calculate": "utility",
    "convert": "utility",
    "weather_check": "utility",
    "set_timer": "utility",
    "network_speed_test": "utility",
    "quick_answer": "utility",
    "ping_host": "utility",
    # Fun
    "joke": "fun",
    "random_fact": "fun",
    # Other
    "speak_text": "voice",
    "valorant_stats": "gaming",
    "gmail_read": "email",
    "gmail_compose": "email",
    "summarize_inbox": "email",
    "calendar_agenda": "calendar",
    "calendar_create_event": "calendar",
    "calendar_update_event": "calendar",
    "watch_reels": "social",
    "stop_watching_reels": "social",
}

ALWAYS_ON_GROUPS = frozenset({"core", "memory", "utility", "terminal"})

# Keyword → groups to include for this turn (union with ALWAYS_ON).
KEYWORD_GROUPS: list[tuple[tuple[str, ...], frozenset[str]]] = [
    (
        ("file", "folder", "directory", "path", "read", "write", "save", "document", "pdf", "find"),
        frozenset({"files"}),
    ),
    (
        (
            "open",
            "launch",
            "app",
            "application",
            "firefox",
            "browser",
            "code",
            "vscode",
            "terminal",
        ),
        frozenset({"apps", "files"}),
    ),
    (
        ("spotify", "music", "song", "playlist", "play", "pause", "volume", "track", "artist"),
        frozenset({"media"}),
    ),
    (
        (
            "search",
            "google",
            "web",
            "website",
            "site",
            "portfolio",
            "internet",
            "news",
            "wikipedia",
            "url",
            "http",
            "browse",
        ),
        frozenset({"web"}),
    ),
    (
        ("youtube", "video", "watch"),
        frozenset({"web", "media"}),
    ),
    (
        ("cpu", "ram", "memory usage", "battery", "system", "disk", "temperature", "process"),
        frozenset({"system"}),
    ),
    (
        ("clipboard", "screenshot", "window", "brightness", "notify", "notification", "desktop"),
        frozenset({"desktop"}),
    ),
    (
        ("shell", "bash", "command", "run ", "execute", "script", "pip ", "npm ", "git "),
        frozenset({"terminal"}),
    ),
    (
        ("codex", "build", "project", "implement", "scaffold", "debug", "develop"),
        frozenset({"codex", "files"}),
    ),
    (
        ("speak", "say ", "voice", "tts"),
        frozenset({"voice"}),
    ),
    (
        ("mail", "email", "gmail", "inbox", "compose", "recipient"),
        frozenset({"email"}),
    ),
    (
        (
            "calendar",
            "calender",
            "schedule",
            "agenda",
            "meeting",
            "appointment",
            "event",
            "birthday",
            "birthdays",
        ),
        frozenset({"calendar"}),
    ),
    (
        ("instagram", "reel", "reels"),
        frozenset({"social"}),
    ),
    (
        ("valorant", "game", "rank", "rr "),
        frozenset({"gaming"}),
    ),
    (
        ("joke", "funny", "laugh"),
        frozenset({"fun"}),
    ),
    (
        ("translate", "dictionary", "define", "meaning"),
        frozenset({"web", "utility"}),
    ),
    (
        ("calculate", "math", "convert", "celsius", "fahrenheit"),
        frozenset({"utility"}),
    ),
    (
        ("remember", "forget", "preference", "memory", "recall"),
        frozenset({"memory"}),
    ),
]


def group_for(tool_name: str) -> str:
    return TOOL_GROUPS.get(tool_name, "misc")


def select_groups_for_message(user_text: str, enabled_groups: list[str] | None = None) -> set[str]:
    """Pick tool groups relevant to the latest user message."""
    text = (user_text or "").lower()
    selected: set[str] = set(ALWAYS_ON_GROUPS)
    selected.add("misc")

    if enabled_groups:
        # Explicit allow-list of groups from config/env.
        selected |= set(enabled_groups)
        return selected

    for keywords, groups in KEYWORD_GROUPS:
        if any(k in text for k in keywords):
            selected |= set(groups)

    # If nothing domain-specific matched, keep it lean — only ALWAYS_ON groups.

    return selected


def filter_tools_payload(
    tools_payload: list[dict[str, object]],
    user_text: str,
    enabled_groups: list[str] | None = None,
    smart: bool = True,
) -> list[dict[str, object]]:
    """Return a (possibly filtered) OpenAI tools payload for this turn."""
    if not smart and not enabled_groups:
        return tools_payload

    groups = select_groups_for_message(user_text, enabled_groups)
    specialist_group = "calendar" if "calendar" in groups else None
    filtered: list[dict[str, object]] = []
    for tool in tools_payload:
        try:
            name = tool["function"]["name"]  # type: ignore[index]
        except Exception:
            filtered.append(tool)
            continue
        g = group_for(str(name))
        if specialist_group and g in {"terminal", "apps", "files", "web"}:
            # Calendar requests must not silently degrade into shell output or
            # app search. Those tools cannot read or mutate Google Calendar.
            continue
        if g in groups or g == "misc":
            filtered.append(tool)

    # Safety: never send zero tools if we had some.
    if not filtered and tools_payload:
        return tools_payload
    return filtered
