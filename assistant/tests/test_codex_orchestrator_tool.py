from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import assistant.tools.codex_orchestrator_tool as module
from assistant.tools.codex_orchestrator_tool import CodexOrchestrateTool


class CodexOrchestratorToolTests(unittest.TestCase):
    def test_rejects_path_like_project_name(self) -> None:
        result = CodexOrchestrateTool().execute(
            {"task": "Create a test app", "project_name": "../outside"},
            SimpleNamespace(report_progress=None),
        )
        assert not result["success"]
        assert "lowercase letters" in result["error"]

    def test_runs_codex_in_contained_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "codex_workspace"
            with (
                patch.object(module, "CODEX_WORKSPACE", workspace),
                patch.object(module.shutil, "which", return_value="/usr/bin/kitty"),
                patch.object(module.subprocess, "Popen") as popen,
                patch.dict(os.environ, {"OPENAI_API_KEY": "private", "LLM_API_KEY": "private"}),
            ):
                result = CodexOrchestrateTool().execute(
                    {
                        "task": "Create a test app",
                        "project_name": "test-app",
                        "model": "terra",
                    },
                    SimpleNamespace(report_progress=None),
                )

            command = popen.call_args.args[0]
            project = workspace / "test-app"
            assert result["success"]
            assert result["created_project"]
            assert result["workspace"] == str(project)
            assert command[:2] == ["kitty", "--directory"]
            shell_command = command[-1]
            assert "workspace-write" in shell_command
            assert "--ask-for-approval never" in shell_command
            assert "--model gpt-5.6-terra" in shell_command
            assert result["model"] == "gpt-5.6-terra"
            assert "codex" in shell_command
            assert popen.call_args.kwargs["cwd"] == project
            assert "OPENAI_API_KEY" not in popen.call_args.kwargs["env"]
            assert "LLM_API_KEY" not in popen.call_args.kwargs["env"]

    def test_checks_dependencies_before_creating_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "codex_workspace"
            with (
                patch.object(module, "CODEX_WORKSPACE", workspace),
                patch.object(module.shutil, "which", return_value=None),
            ):
                result = CodexOrchestrateTool().execute(
                    {"task": "Create a test app", "project_name": "test-app"},
                    SimpleNamespace(report_progress=None),
                )

            assert not result["success"]
            assert "Kitty" in result["error"]
            assert not workspace.exists()


if __name__ == "__main__":
    unittest.main()
