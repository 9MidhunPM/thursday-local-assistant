from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


def _playerctl_available() -> bool:
    return shutil.which("playerctl") is not None


def _run_playerctl(args: list[str], player: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = ["playerctl"]
    if player:
        cmd.extend(["--player", player])
    return subprocess.run(
        [*cmd, *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def _list_players() -> list[str]:
    result = _run_playerctl(["-l"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _select_spotify_player(players: list[str]) -> str | None:
    for player in players:
        if "spotify" in player.lower():
            return player
    return None


def _ensure_spotify_running() -> str | None:
    players = _list_players()
    player = _select_spotify_player(players)
    if player:
        return player
    if shutil.which("spotify"):
        subprocess.Popen(
            ["spotify"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            time.sleep(0.5)
            players = _list_players()
            player = _select_spotify_player(players)
            if player:
                for _ in range(20):
                    if _spotify_window_address():
                        break
                    time.sleep(0.25)
                return player
    return None

def _spotify_window_address() -> str | None:
    if not shutil.which("hyprctl"):
        return None
    res = subprocess.run(
        ["hyprctl", "clients", "-j"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if res.returncode != 0:
        return None
    try:
        clients = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    for client in clients:
        window_class = str(client.get("class", "")).lower()
        initial_class = str(client.get("initialClass", "")).lower()
        title = str(client.get("title", "")).lower()
        if "spotify" in window_class or "spotify" in initial_class or "spotify" in title:
            address = client.get("address")
            if isinstance(address, str) and address:
                return address
    return None


def _focus_spotify() -> tuple[bool, str | None]:
    """Bring the real Spotify window to the active workspace and focus it."""
    if not shutil.which("hyprctl"):
        return False, "Hyprland control is unavailable."
    address = _spotify_window_address()
    if not address:
        return False, "Spotify opened, but its window was not found."

    legacy = subprocess.run(
        ["hyprctl", "dispatch", "focuswindow", f"address:{address}"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    legacy_output = f"{legacy.stdout}\n{legacy.stderr}".lower()
    if legacy.returncode == 0 and "error" not in legacy_output:
        return True, None

    lua = subprocess.run(
        [
            "hyprctl",
            "dispatch",
            f'hl.dsp.focus({{ window = "address:{address}" }})',
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    lua_output = f"{lua.stdout}\n{lua.stderr}".lower()
    if lua.returncode == 0 and "error" not in lua_output:
        return True, None
    return False, lua.stderr.strip() or legacy.stderr.strip() or "Spotify could not be focused."


def _run_wtype(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["wtype", *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _send_spotify_shortcut(key: str, modifiers: str = "") -> tuple[bool, str | None]:
    """Send a shortcut directly to Spotify, with wtype as the compositor fallback."""
    address = _spotify_window_address()
    if shutil.which("hyprctl") and address:
        result = subprocess.run(
            [
                "hyprctl",
                "dispatch",
                "hl.dsp.send_shortcut({ "
                f'mods = "{modifiers}", key = "{key}", window = "address:{address}"'
                " })",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        output = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode == 0 and "error" not in output:
            return True, None

    args: list[str] = []
    if modifiers:
        for modifier in modifiers.casefold().split():
            args.extend(["-M", modifier])
    args.extend(["-k", key])
    if modifiers:
        for modifier in reversed(modifiers.casefold().split()):
            args.extend(["-m", modifier])
    fallback = _run_wtype(args)
    if fallback.returncode == 0:
        return True, None
    return False, fallback.stderr.strip() or f"Spotify shortcut {key} failed."


def _paste_spotify_query(query: str) -> tuple[bool, str | None]:
    """Paste text without depending on the user's active keyboard layout."""
    if not shutil.which("wl-copy") or not shutil.which("wl-paste"):
        typed = _run_wtype(["--", query])
        if typed.returncode == 0:
            return True, None
        return False, typed.stderr.strip() or "Spotify search input failed."

    previous = subprocess.run(
        ["wl-paste", "--no-newline"],
        capture_output=True,
        timeout=5,
        check=False,
    )
    copied = subprocess.run(
        ["wl-copy"],
        input=query.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if copied.returncode != 0:
        return False, "Clipboard input failed."

    try:
        pasted, paste_error = _send_spotify_shortcut("V", "CTRL")
        if not pasted:
            return False, paste_error or "Spotify search paste failed."
        time.sleep(0.2)
        return True, None
    finally:
        if previous.returncode == 0:
            subprocess.run(
                ["wl-copy"],
                input=previous.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )


def _spotify_desktop_search(query: str, player: str) -> tuple[bool, str | None]:
    """Use Spotify's focused quick-search UI and activate its first result."""
    if not shutil.which("wtype"):
        return False, "wtype is not installed."
    focused, focus_error = _focus_spotify()
    if not focused:
        return False, focus_error

    old_meta = _run_playerctl(
        ["metadata", "--format", "{{mpris:trackid}}\t{{artist}} - {{title}}"],
        player=player,
    )
    old_song = old_meta.stdout.strip() if old_meta.returncode == 0 else ""

    # Ctrl+K opens Spotify's search overlay. Clipboard paste avoids wtype text
    # being remapped by a custom keyboard layout in Spotify's XWayland window.
    # Spotify highlights its highest-ranked result automatically; Shift+Enter
    # is the UI's advertised "Play" shortcut for that selected row.
    for key in ("K", "A"):
        sent, shortcut_error = _send_spotify_shortcut(key, "CTRL")
        if not sent:
            return False, shortcut_error or "Spotify search input failed."
        time.sleep(0.15)

    pasted, paste_error = _paste_spotify_query(query)
    if not pasted:
        return False, paste_error

    time.sleep(1.25)
    sent, shortcut_error = _send_spotify_shortcut("RETURN", "SHIFT")
    if not sent:
        return False, shortcut_error or "Spotify result selection failed."

    for attempt in range(20):
        time.sleep(0.25)
        meta = _run_playerctl(
            ["metadata", "--format", "{{mpris:trackid}}\t{{artist}} - {{title}}"],
            player=player,
        )
        current_song = meta.stdout.strip() if meta.returncode == 0 else ""
        if current_song and current_song != old_song:
            _run_playerctl(["play"], player=player)
            return True, current_song.split("\t", 1)[-1]

    # Replaying the already-active first result does not change metadata. Only
    # accept it when Spotify itself confirms playback.
    status = _run_playerctl(["status"], player=player)
    current = _run_playerctl(
        ["metadata", "--format", "{{artist}} - {{title}}"], player=player
    )
    current_text = current.stdout.strip() if current.returncode == 0 else ""
    query_words = re.findall(r"[a-z0-9]+", query.casefold())
    metadata_words = set(re.findall(r"[a-z0-9]+", current_text.casefold()))
    if (
        status.returncode == 0
        and status.stdout.strip().lower() == "playing"
        and current_text
        and query_words
        and all(word in metadata_words for word in query_words)
    ):
        return True, current_text
    return False, "Spotify did not confirm that the first search result started playing."


def _spotify_prefs_path() -> Path:
    return Path.home() / ".config" / "spotify" / "prefs"


def _spotify_username() -> str | None:
    prefs_path = _spotify_prefs_path()
    if not prefs_path.exists():
        return None
    text = prefs_path.read_text(encoding="utf-8", errors="ignore")
    for key in ("autologin.canonical_username", "autologin.username"):
        match = re.search(rf'{re.escape(key)}="([^"]+)"', text)
        if match:
            return match.group(1)
    return None


def _open_spotify_uri(uri: str) -> tuple[bool, str | None]:
    player = _ensure_spotify_running()
    if player:
        open_result = _run_playerctl(["open", uri], player=player)
        if open_result.returncode == 0:
            return True, None
    if shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", uri],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, None
    return False, "Unable to open Spotify URI."


def _open_spotify_url(url: str) -> tuple[bool, str | None]:
    if shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", url],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, None
    return False, "Unable to open Spotify URL."


def _normalize_playlist_uri(value: str) -> str | None:
    stripped = value.strip()
    if stripped.startswith("spotify:"):
        return stripped
    if "open.spotify.com/" in stripped:
        match = re.search(r"/playlist/([A-Za-z0-9]+)", stripped)
        if match:
            return f"spotify:playlist:{match.group(1)}"
    return None


@dataclass
class SpotifySearchPlayTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="spotify_search_play",
        description=(
            "Focus the Spotify desktop app, search for a song, and play the first visible "
            "Spotify result. Never controls another media player."
        ),
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"success": False, "error": "Query is required."}
        if not _playerctl_available():
            return {"success": False, "error": "playerctl is not installed."}

        player = _ensure_spotify_running()
        if not player:
            return {"success": False, "error": "Spotify is not available."}
            
        query = query.strip()
        if query.startswith("spotify:"):
            target_uri = query
        elif "open.spotify.com/" in query:
            # Convert URL to URI: https://open.spotify.com/track/123 -> spotify:track:123
            match = re.search(r"open\.spotify\.com/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)", query)
            if match:
                target_uri = f"spotify:{match.group(1)}:{match.group(2)}"
            else:
                return {"success": False, "error": "The Spotify URL is not recognized."}
        else:
            success, result = _spotify_desktop_search(query, player)
            if not success:
                return {"success": False, "error": result or "Spotify search failed."}
            _focus_spotify()
            return {
                "success": True,
                "output": f"Playing the first Spotify result: {result or query}",
            }

        focused, focus_error = _focus_spotify()
        if not focused:
            return {"success": False, "error": focus_error or "Spotify could not be focused."}
        
        old_metadata = _run_playerctl(
            ["metadata", "--format", "{{artist}} - {{title}}"], player=player
        )
        old_song = old_metadata.stdout.strip() if old_metadata.returncode == 0 else ""

        open_result = _run_playerctl(["open", target_uri], player=player)
        if open_result.returncode != 0:
            if shutil.which("xdg-open"):
                subprocess.Popen(
                    ["xdg-open", target_uri],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        time.sleep(1.0)

        play_result = _run_playerctl(["play"], player=player)
        if play_result.returncode != 0:
            return {
                "success": False,
                "error": play_result.stderr.strip() or "Spotify play failed.",
            }
            
        song_info = "Unknown song"
        for _ in range(15):
            time.sleep(0.2)
            meta_res = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
            if meta_res.returncode == 0:
                current_song = meta_res.stdout.strip()
                if current_song and current_song != old_song:
                    song_info = current_song
                    break
                if not old_song and current_song:
                    song_info = current_song
                    break
        
        status = _run_playerctl(["status"], player=player)
        if status.returncode == 0 and status.stdout.strip().lower() == "playing":
            if song_info == "Unknown song":
                meta_res = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
                song_info = meta_res.stdout.strip() if meta_res.returncode == 0 else "Unknown song"
            return {"success": True, "output": f"Playing on Spotify: {song_info}"}
            
        return {
            "success": True,
            "output": "Spotify playback was requested.",
            "warning": "Playback state not confirmed.",
        }


@dataclass
class SpotifyControlTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="spotify_control",
        description="Control Spotify playback: status (check what is currently playing), resume (unpause), pause, next, previous.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "play", "pause", "next", "previous"],
                }
            },
            "required": ["action"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        if not isinstance(action, str):
            return {"success": False, "error": "Action is required."}
        if not _playerctl_available():
            return {"success": False, "error": "playerctl is not installed."}
        player = _ensure_spotify_running()
        if not player:
            return {"success": False, "error": "Spotify is not available."}
            
        if action != "status":
            _focus_spotify()
            
        if action == "status":
            status_res = _run_playerctl(["status"], player=player)
            meta_res = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
            if status_res.returncode == 0:
                state = status_res.stdout.strip()
                song = meta_res.stdout.strip() if meta_res.returncode == 0 else "Unknown song"
                return {"success": True, "output": f"Spotify is currently {state}. Playing: {song}"}
            return {"success": False, "error": "Could not retrieve playback status."}
            
        if action in ("next", "previous"):
            old_metadata = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
            old_song = old_metadata.stdout.strip() if old_metadata.returncode == 0 else ""
            
            result = _run_playerctl([action], player=player)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip() or f"Spotify {action} failed."}
                
            # Wait for metadata to update
            song_info = "Unknown song"
            for _ in range(15):
                time.sleep(0.2)
                meta_res = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
                if meta_res.returncode == 0:
                    current_song = meta_res.stdout.strip()
                    if current_song and current_song != old_song:
                        song_info = current_song
                        break
            _focus_spotify()
            return {"success": True, "output": f"Spotify {action} executed. Now playing: {song_info}"}

        result = _run_playerctl([action], player=player)
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip() or "Spotify control failed."}
        _focus_spotify()
        return {"success": True, "output": f"Spotify {action} executed."}


@dataclass
class SpotifyLibraryTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="spotify_library",
        description="Open Spotify liked songs or your playlists library.",
        parameters={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["liked_songs", "playlists"],
                }
            },
            "required": ["section"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        section = arguments.get("section")
        if not isinstance(section, str):
            return {"success": False, "error": "Section is required."}
        if section == "liked_songs":
            success, error = _open_spotify_uri("spotify:collection:tracks")
            if not success:
                return {"success": False, "error": error or "Failed to open liked songs."}
            _focus_spotify()
            return {"success": True, "output": "Opened Spotify liked songs."}
        success, error = _open_spotify_uri("spotify:collection:playlists")
        if not success:
            return {"success": False, "error": error or "Failed to open playlists."}
        _focus_spotify()
        return {"success": True, "output": "Opened your Spotify playlists."}


@dataclass
class SpotifyPlaylistTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="spotify_play_playlist",
        description="Open or play a Spotify playlist using a playlist URI, URL, or name. DO NOT use this for single songs.",
        parameters={
            "type": "object",
            "properties": {
                "playlist": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["open", "play"],
                },
            },
            "required": ["playlist"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        playlist = arguments.get("playlist")
        action = arguments.get("action", "play")
        if not isinstance(playlist, str):
            return {"success": False, "error": "Playlist is required."}
        if not isinstance(action, str):
            return {"success": False, "error": "Action must be a string."}
        uri = _normalize_playlist_uri(playlist)
        if uri is None:
            return {
                "success": False,
                "error": (
                    "Playlist name alone is ambiguous. Open your playlists library first or pass "
                    "an exact Spotify playlist URL or URI."
                ),
            }
        success, error = _open_spotify_uri(uri)
        if not success:
            return {"success": False, "error": error or "Failed to open playlist."}
        if action == "play":
            player = _ensure_spotify_running()
            if player:
                play_result = _run_playerctl(["play"], player=player)
                if play_result.returncode != 0:
                    _focus_spotify()
                    return {
                        "success": True,
                        "output": "Opened Spotify playlist.",
                        "warning": play_result.stderr.strip() or "Playback could not be confirmed.",
                    }
        _focus_spotify()
        return {"success": True, "output": f"Spotify playlist {action} requested."}


def get_tools() -> list[BaseTool]:
    return [
        SpotifySearchPlayTool(),
        SpotifyControlTool(),
        SpotifyLibraryTool(),
        SpotifyPlaylistTool(),
    ]
