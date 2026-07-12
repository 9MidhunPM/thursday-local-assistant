from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from assistant.config.loader import load_config  # noqa: E402
from assistant.memory.long_term import LongTermMemory  # noqa: E402
from assistant.runtime import build_runtime, default_config_path  # noqa: E402
from assistant.tools.registry import ToolRegistry  # noqa: E402


class SmokeTests(unittest.TestCase):
    def test_config_and_tools_load(self) -> None:
        config_path = ROOT / "assistant" / "config" / "config.json"
        config = load_config(config_path)
        registry = ToolRegistry.load_builtin(config.tools)
        tool_names = {tool.name for tool in registry.tools()}
        expected = {
            "open_app",
            "search_apps",
            "open_path",
            "web_search",
            "fetch_page",
            "read_file",
            "write_file",
            "search_files",
            "find_folders",
            "current_time",
            "current_date",
            "system_info",
            "battery_info",
            "memory_usage",
            "cpu_usage",
            "run_terminal_command",
            "store_preference",
            "get_preference",
            "store_memory",
            "recall_memory",
            "store_fact",
            "search_personal_knowledge",
            "get_entity_profile",
            "spotify_search_play",
            "spotify_control",
            "spotify_library",
            "spotify_play_playlist",
            "speak_text",
            "gmail_read",
        }
        missing = expected - tool_names
        self.assertFalse(missing, f"Missing tools: {missing}")

    def test_long_term_memory_roundtrip(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.db"
            memory = LongTermMemory(db_path)
            memory.set_preference("editor", "vscode", "2026-06-04T12:00:00Z")
            pref = memory.get_preference("editor")
            self.assertIsNotNone(pref)
            self.assertEqual(pref.value, "vscode")
            memory.store_memory("favorite", "VS Code", "2026-06-04T12:00:00Z")
            memo = memory.recall_memory("favorite")
            self.assertIsNotNone(memo)
            self.assertEqual(memo.content, "VS Code")
            memory.remember_fact("midhun", "likes", "local-first assistants", "2026-06-04T12:00:00Z")
            facts = memory.get_entity_facts("midhun")
            self.assertEqual(len(facts), 1)
            context = memory.build_context("assistant")
            self.assertTrue(context["facts"])

    def test_config_has_voice_section(self) -> None:
        config_path = ROOT / "assistant" / "config" / "config.json"
        config = load_config(config_path)
        self.assertTrue(config.voice.tts_enabled)
        self.assertTrue(config.voice.stt_enabled)
        self.assertEqual(config.voice.tts_voice, "en-US-EmmaMultilingualNeural")

    def test_runtime_builds(self) -> None:
        runtime = build_runtime(default_config_path())
        self.assertIsNotNone(runtime.agent)
        self.assertTrue(runtime.config.model.model)
        self.assertIsNotNone(runtime.config.voice)


if __name__ == "__main__":
    unittest.main()
