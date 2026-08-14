from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from assistant.tools.spotify_tools import (
    SpotifySearchPlayTool,
    _select_spotify_player,
    _spotify_desktop_search,
)


def _result(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class SpotifyToolTests(unittest.TestCase):
    def test_player_selection_never_falls_back_to_other_media(self) -> None:
        self.assertIsNone(_select_spotify_player(["chromium.instance1", "mpv"]))
        self.assertEqual(
            _select_spotify_player(["chromium.instance1", "spotify.instance42"]),
            "spotify.instance42",
        )

    @patch("assistant.tools.spotify_tools._ensure_spotify_running", return_value=None)
    @patch("assistant.tools.spotify_tools._playerctl_available", return_value=True)
    def test_search_refuses_when_only_non_spotify_players_exist(self, _available, _ensure) -> None:
        result = SpotifySearchPlayTool().execute({"query": "End of Beginning"}, None)  # type: ignore[arg-type]
        self.assertFalse(result["success"])
        self.assertIn("Spotify", result["error"])

    @patch("assistant.tools.spotify_tools.time.sleep")
    @patch("assistant.tools.spotify_tools._paste_spotify_query", return_value=(True, None))
    @patch("assistant.tools.spotify_tools._send_spotify_shortcut", return_value=(True, None))
    @patch("assistant.tools.spotify_tools._run_playerctl")
    @patch("assistant.tools.spotify_tools._focus_spotify", return_value=(True, None))
    @patch("assistant.tools.spotify_tools.shutil.which", return_value="/usr/bin/wtype")
    def test_desktop_search_activates_first_result(
        self,
        _which,
        _focus,
        playerctl,
        send_shortcut,
        paste_query,
        _sleep,
    ) -> None:
        playerctl.side_effect = [
            _result("old-id\tOld Artist - Old Song\n"),
            _result("new-id\tDjo - End of Beginning\n"),
            _result(),
        ]
        success, song = _spotify_desktop_search("End of Beginning", "spotify.instance42")

        self.assertTrue(success)
        self.assertEqual(song, "Djo - End of Beginning")
        paste_query.assert_called_once_with("End of Beginning")
        self.assertEqual(
            [call.args for call in send_shortcut.call_args_list],
            [("K", "CTRL"), ("A", "CTRL"), ("RETURN", "SHIFT")],
        )


if __name__ == "__main__":
    unittest.main()
