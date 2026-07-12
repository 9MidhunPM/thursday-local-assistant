from __future__ import annotations

import unittest

from assistant.tools.groups import filter_tools_payload, select_groups_for_message


class ToolGroupTests(unittest.TestCase):
    def test_music_selects_media(self) -> None:
        groups = select_groups_for_message("play bohemian rhapsody on spotify")
        self.assertIn("media", groups)
        self.assertIn("memory", groups)

    def test_filter_payload(self) -> None:
        payload = [
            {"type": "function", "function": {"name": "spotify_search_play", "description": "", "parameters": {}}},
            {"type": "function", "function": {"name": "valorant_stats", "description": "", "parameters": {}}},
            {"type": "function", "function": {"name": "current_time", "description": "", "parameters": {}}},
        ]
        filtered = filter_tools_payload(payload, "play a song", smart=True)
        names = {t["function"]["name"] for t in filtered}  # type: ignore[index]
        self.assertIn("spotify_search_play", names)
        self.assertIn("current_time", names)
        # gaming should usually drop for a pure music request
        self.assertNotIn("valorant_stats", names)


if __name__ == "__main__":
    unittest.main()
