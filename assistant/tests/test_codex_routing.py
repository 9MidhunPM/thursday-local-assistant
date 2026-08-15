from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from assistant.agent.agent import Agent, _parse_codex_launch


class CodexRoutingTests(unittest.TestCase):
    def test_codex_ui_launch_bypasses_llm_and_runs_orchestrator(self) -> None:
        agent = object.__new__(Agent)
        agent._loggers = SimpleNamespace(user=MagicMock(), model=MagicMock())
        agent._short_term = MagicMock()
        agent._maybe_extract = MagicMock()
        agent._execute_tool = MagicMock(
            return_value={
                "tool": "codex_orchestrate",
                "success": True,
                "workspace": "/tmp/codex_workspace/todo-app",
                "output": "Built and tested the app.",
            }
        )
        on_call = MagicMock()
        on_result = MagicMock()

        response = agent.handle_message(
            (
                '[codex-launch]\n'
                '{"project_name":"todo-app","model":"gpt-5.6","brief":"Build a todo app"}'
            ),
            on_tool_call=on_call,
            on_tool_result=on_result,
        )

        agent._execute_tool.assert_called_once()
        name, arguments = agent._execute_tool.call_args.args[:2]
        assert name == "codex_orchestrate"
        assert arguments["project_name"] == "todo-app"
        assert arguments["model"] == "gpt-5.6"
        assert "Project brief:\nBuild a todo app" in arguments["task"]
        on_call.assert_called_once()
        on_result.assert_called_once()
        assert "started an interactive project session" in response
        assert "Built and tested" in response

    def test_rejects_malformed_codex_launch_envelope(self) -> None:
        assert _parse_codex_launch("[codex-launch] not json") is None


if __name__ == "__main__":
    unittest.main()
