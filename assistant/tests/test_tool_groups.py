from __future__ import annotations

import unittest

from assistant.agent.agent import _is_contextual_action_follow_up, _is_retry_follow_up
from assistant.tools.groups import filter_tools_payload, select_groups_for_message


class ToolGroupTests(unittest.TestCase):
    def test_retry_follow_up_detection(self) -> None:
        self.assertTrue(_is_retry_follow_up("try again now"))
        self.assertTrue(_is_retry_follow_up("Retry!"))
        self.assertFalse(_is_retry_follow_up("try another email address"))

    def test_terminal_is_always_available(self) -> None:
        groups = select_groups_for_message("show me what is taking space")
        self.assertIn("terminal", groups)

    def test_music_selects_media(self) -> None:
        groups = select_groups_for_message("play bohemian rhapsody on spotify")
        self.assertIn("media", groups)
        self.assertIn("memory", groups)

    def test_reels_selects_social_tools(self) -> None:
        self.assertIn("social", select_groups_for_message("watch reels"))

    def test_calendar_selects_calendar_tools(self) -> None:
        self.assertIn("calendar", select_groups_for_message("what is on my calendar tomorrow"))
        groups = select_groups_for_message("list september birthdays in my calender")
        self.assertIn("calendar", groups)

    def test_contextual_calendar_follow_up_detection(self) -> None:
        self.assertTrue(_is_contextual_action_follow_up("open it"))
        self.assertTrue(_is_contextual_action_follow_up("use calender agenda"))

    def test_calendar_filter_excludes_fake_fallback_tools(self) -> None:
        payload = [
            {"type": "function", "function": {"name": "calendar_agenda"}},
            {"type": "function", "function": {"name": "run_terminal_command"}},
            {"type": "function", "function": {"name": "open_app"}},
        ]
        filtered = filter_tools_payload(payload, "check my calender", smart=True)
        names = {t["function"]["name"] for t in filtered}  # type: ignore[index]
        self.assertEqual(names, {"calendar_agenda"})

    def test_inbox_summary_selects_email_tools(self) -> None:
        self.assertIn("email", select_groups_for_message("summarize my inbox"))

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
