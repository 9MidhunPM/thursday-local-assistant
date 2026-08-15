from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path  # noqa: TC003 - used to build the runtime workspace path.
from typing import TYPE_CHECKING, Any

from assistant.config.loader import PROJECT_ROOT
from assistant.tools.base import BaseTool, ToolMetadata

if TYPE_CHECKING:
    from assistant.agent.context import ExecutionContext


CODEX_WORKSPACE = PROJECT_ROOT / "codex_workspace"
_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
_MODEL_ALIASES = {
    "terra": "gpt-5.6-terra",
    "luna": "gpt-5.6-luna",
    "sol": "gpt-5.6-sol",
}


def _project_directory(project_name: str | None) -> tuple[Path, str | None] | None:
    """Return a workspace-contained project directory, rejecting path-like names."""
    if project_name is None or not project_name.strip():
        return CODEX_WORKSPACE, None
    normalized = project_name.strip().lower().replace("_", "-")
    if not _PROJECT_NAME.fullmatch(normalized):
        return None
    return CODEX_WORKSPACE / normalized, normalized


class CodexOrchestrateTool(BaseTool):
    metadata = ToolMetadata(
        name="codex_orchestrate",
        description=(
            "Open an interactive Kitty terminal running the local Codex CLI for a software project "
            "task. Codex can create and edit only projects inside Thursday's dedicated "
            "codex_workspace folder. Use for building, extending, debugging, or reviewing a "
            "project."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Concrete implementation task for Codex.",
                },
                "project_name": {
                    "type": "string",
                    "description": "Optional project folder name inside codex_workspace.",
                },
                "model": {
                    "type": "string",
                    "description": "Optional Codex model identifier. Omit to use Codex's default.",
                },
            },
            "required": ["task"],
        },
    )

    def execute(self, arguments: dict[str, Any], _context: ExecutionContext) -> dict[str, Any]:
        task = arguments.get("task")
        if not isinstance(task, str) or not task.strip():
            return {"success": False, "error": "A concrete Codex task is required."}
        if len(task) > 20_000:
            return {
                "success": False,
                "error": "Codex task is too long (maximum 20,000 characters).",
            }

        project_name = arguments.get("project_name")
        if project_name is not None and not isinstance(project_name, str):
            return {"success": False, "error": "project_name must be text when supplied."}
        project = _project_directory(project_name)
        if project is None:
            return {
                "success": False,
                "error": "project_name must use lowercase letters, numbers, and hyphens only.",
            }
        project_dir, normalized_name = project

        requested_model = arguments.get("model")
        if requested_model is not None and (
            not isinstance(requested_model, str) or not _MODEL_NAME.fullmatch(requested_model)
        ):
            return {"success": False, "error": "model must be a valid Codex model identifier."}
        model = (
            _MODEL_ALIASES.get(requested_model.casefold(), requested_model)
            if requested_model
            else None
        )

        if shutil.which("kitty") is None:
            return {
                "success": False,
                "error": "Kitty is not installed or is not on Thursday's PATH.",
                "workspace": str(project_dir),
            }
        if shutil.which("codex") is None:
            return {
                "success": False,
                "error": "The Codex CLI is not installed or is not on Thursday's PATH.",
                "workspace": str(project_dir),
            }

        existed = project_dir.exists()
        project_dir.mkdir(parents=True, exist_ok=True)

        # Do not pass Thursday's provider credential to a child process. Codex
        # authenticates through its own local session and stays sandboxed to this project.
        environment = os.environ.copy()
        environment.pop("OPENAI_API_KEY", None)
        environment.pop("LLM_API_KEY", None)
        codex_command = [
            "codex",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            *(["--model", model] if model else []),
            "-C",
            str(project_dir),
            (
                "You are the implementation agent for Thursday. Work only in the current "
                "workspace. Implement the user's request, inspect existing files before editing, "
                "run relevant verification when practical, and finish with a concise summary of "
                "changes and checks. Do not access parent directories or delete unrelated files."
                "\n\n"
                f"User task: {task.strip()}"
            ),
        ]
        kitty_command = [
            "kitty",
            "--directory",
            str(project_dir),
            "--title",
            f"Thursday Codex: {project_dir.name}",
            "--hold",
            "bash",
            "-lc",
            shlex.join(codex_command),
        ]
        try:
            subprocess.Popen(
                kitty_command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=project_dir,
                env=environment,
                start_new_session=True,
            )
        except FileNotFoundError:
            return {
                "success": False,
                "error": "Could not launch Kitty or the Codex CLI.",
                "workspace": str(project_dir),
            }
        return {
            "success": True,
            "workspace": str(project_dir),
            "project_name": normalized_name,
            "model": model,
            "created_project": not existed,
            "output": "Opened a Kitty terminal with the interactive Codex session.",
            "error": None,
            "launched": True,
        }


def get_tools() -> list[BaseTool]:
    return [CodexOrchestrateTool()]
