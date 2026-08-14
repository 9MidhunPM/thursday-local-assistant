from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assistant.agent.safety import SafetyManager, TerminalSafetyRules
from assistant.config.loader import TerminalSafetyConfig, ToolConfig
from assistant.tools.terminal_tools import RunTerminalCommandTool


class _Memory:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def record_command(self, command: str, _timestamp: str) -> None:
        self.commands.append(command)


def _tool(read_roots: list[str]) -> RunTerminalCommandTool:
    terminal = TerminalSafetyConfig(
        allow_shell=False,
        whitelist_commands=[],
        blacklist_patterns=[],
        confirm_patterns=[],
        timeout_sec=15,
    )
    config = ToolConfig(
        read_roots=read_roots,
        write_roots=read_roots,
        app_commands={},
        terminal=terminal,
    )
    safety = SafetyManager(
        TerminalSafetyRules(
            allow_shell=False,
            whitelist_commands=[],
            blacklist_patterns=[],
            confirm_patterns=[],
        )
    )
    return RunTerminalCommandTool(config=config, safety=safety)


class TerminalToolTests(unittest.TestCase):
    def test_command_uses_requested_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = _Memory()
            context = SimpleNamespace(
                confirm=lambda _prompt: True,
                memory=memory,
                now=lambda: datetime.now(UTC),
            )
            result = _tool([tmp]).execute(
                {"command": "pwd", "cwd": tmp}, context  # type: ignore[arg-type]
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], str(Path(tmp).resolve()))
        self.assertEqual(result["return_code"], 0)
        self.assertIsInstance(result["duration_ms"], int)
        self.assertEqual(memory.commands, ["pwd"])

    @patch("assistant.tools.terminal_tools.subprocess.run")
    def test_timeout_returns_structured_failure(self, run) -> None:
        run.side_effect = subprocess.TimeoutExpired("slow-command", 1, output="partial")
        context = SimpleNamespace(
            confirm=lambda _prompt: True,
            memory=_Memory(),
            now=lambda: datetime.now(UTC),
        )

        result = _tool(["/"]).execute(
            {"command": "slow-command", "timeout_sec": 1}, context  # type: ignore[arg-type]
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["output"], "partial")
        self.assertIsNone(result["return_code"])


if __name__ == "__main__":
    unittest.main()
