from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

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


def _select_player(players: list[str]) -> str | None:
    for player in players:
        if "spotify" in player.lower():
            return player
    return players[0] if players else None


def _ensure_spotify_running() -> str | None:
    players = _list_players()
    player = _select_player(players)
    if player:
        return player
    if shutil.which("spotify"):
        subprocess.Popen(
            ["spotify"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(10):
            time.sleep(0.5)
            players = _list_players()
            player = _select_player(players)
            if player:
                return player
    return None

def _show_and_hide_spotify() -> None:
    """Focus Spotify to bring it to the screen."""
    if not shutil.which("hyprctl"):
        return
        
    def get_spotify_address() -> str | None:
        import json
        res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True)
        if res.returncode == 0:
            try:
                clients = json.loads(res.stdout)
                for c in clients:
                    c_class = c.get('class', '').lower()
                    c_title = c.get('title', '').lower()
                    if "spotify" in c_class or "spotify" in c_title:
                        return c.get('address')
            except json.JSONDecodeError:
                pass
        return None

    address = get_spotify_address()
    if address:
        subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{address}"], capture_output=True)


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


def _search_spotify_track(query: str) -> str | None:
    import urllib.request
    import urllib.parse
    import json
    import re
    
    # 1. Enhance query using iTunes API for exact artist and track
    itunes_url = 'https://itunes.apple.com/search?' + urllib.parse.urlencode({'term': query, 'entity': 'song', 'limit': '1'})
    try:
        req_it = urllib.request.Request(itunes_url, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req_it, timeout=3).read().decode('utf-8')
        data = json.loads(res)
        if data.get('resultCount', 0) > 0:
            track = data['results'][0]['trackName']
            artist = data['results'][0]['artistName']
            query = f"{artist} {track}"
    except Exception:
        pass

    # 2. Scrape DDG Lite for the Spotify URI
    url = 'https://lite.duckduckgo.com/lite/'
    search_str = f"{query} spotify track"
    data = urllib.parse.urlencode({'q': search_str}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        html = urllib.request.urlopen(req, timeout=5).read().decode('utf-8', errors='ignore')
        matches = re.findall(r'open\.spotify\.com/track/[a-zA-Z0-9]+', html)
        if matches:
            return f"spotify:track:{matches[0].split('/')[-1]}"
    except Exception:
        pass
    return None


@dataclass
class SpotifySearchPlayTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="spotify_search_play",
        description="Search Spotify and attempt to start playback.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str):
            return {"success": False, "error": "Query is required."}
        if not _playerctl_available():
            return {"success": False, "error": "playerctl is not installed."}

        player = _ensure_spotify_running()
        if not player:
            return {"success": False, "error": "Spotify is not available."}
            
        is_direct_uri = False
        if query.startswith("spotify:"):
            target_uri = query
            is_direct_uri = True
        elif "open.spotify.com/" in query:
            # Convert URL to URI: https://open.spotify.com/track/123 -> spotify:track:123
            match = re.search(r"open\.spotify\.com/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)", query)
            if match:
                target_uri = f"spotify:{match.group(1)}:{match.group(2)}"
                is_direct_uri = True
            else:
                target_uri = query
        else:
            resolved_uri = _search_spotify_track(query)
            if resolved_uri:
                target_uri = resolved_uri
                is_direct_uri = True
            else:
                target_uri = f"spotify:search:{quote_plus(query)}"
            
        # Bring Spotify to the screen BEFORE the action starts so the user can watch it
        _show_and_hide_spotify()
        
        open_result = _run_playerctl(["open", target_uri], player=player)
        if open_result.returncode != 0:
            if shutil.which("xdg-open"):
                subprocess.Popen(
                    ["xdg-open", target_uri],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        # If it was a direct URI, it usually starts playing automatically, but we might still want to ensure it plays
        # Wait a moment for it to load
        time.sleep(1.0)

        old_metadata = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
        old_song = old_metadata.stdout.strip() if old_metadata.returncode == 0 else ""

        play_result = _run_playerctl(["play"], player=player)
        if play_result.returncode != 0:
            return {
                "success": False,
                "error": play_result.stderr.strip() or "Spotify play failed.",
            }
            
        # Poll up to 3 seconds for metadata to change
        song_info = "Unknown song"
        for _ in range(15):
            time.sleep(0.2)
            meta_res = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
            if meta_res.returncode == 0:
                current_song = meta_res.stdout.strip()
                if current_song and current_song != old_song:
                    song_info = current_song
                    break
                # If old_song was empty and we got something, use it
                if not old_song and current_song:
                    song_info = current_song
                    break
        
        status = _run_playerctl(["status"], player=player)
        if status.returncode == 0 and status.stdout.strip().lower() == "playing":
            # If we timed out waiting for change, just use whatever it's currently at
            if song_info == "Unknown song":
                meta_res = _run_playerctl(["metadata", "--format", "{{artist}} - {{title}}"], player=player)
                song_info = meta_res.stdout.strip() if meta_res.returncode == 0 else "Unknown song"
            return {"success": True, "output": f"Searching and playing on Spotify. Currently playing: {song_info}"}
            
        return {
            "success": True,
            "output": "Search triggered and play requested on Spotify.",
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
            _show_and_hide_spotify()
            
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
            _show_and_hide_spotify()
            return {"success": True, "output": f"Spotify {action} executed. Now playing: {song_info}"}

        result = _run_playerctl([action], player=player)
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip() or "Spotify control failed."}
        _show_and_hide_spotify()
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
            _show_and_hide_spotify()
            return {"success": True, "output": "Opened Spotify liked songs."}
        success, error = _open_spotify_uri("spotify:collection:playlists")
        if not success:
            return {"success": False, "error": error or "Failed to open playlists."}
        _show_and_hide_spotify()
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
                    _show_and_hide_spotify()
                    return {
                        "success": True,
                        "output": "Opened Spotify playlist.",
                        "warning": play_result.stderr.strip() or "Playback could not be confirmed.",
                    }
        _show_and_hide_spotify()
        return {"success": True, "output": f"Spotify playlist {action} requested."}


def get_tools() -> list[BaseTool]:
    return [
        SpotifySearchPlayTool(),
        SpotifyControlTool(),
        SpotifyLibraryTool(),
        SpotifyPlaylistTool(),
    ]
