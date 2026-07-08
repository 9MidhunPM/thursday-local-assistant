from __future__ import annotations

import configparser
import difflib
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.config.loader import ToolConfig
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass(frozen=True)
class AppEntry:
    name: str
    desktop_id: str
    exec_cmd: str
    keywords: list[str]


class AppIndex:
    def __init__(self) -> None:
        self._apps = self._load_apps()

    @staticmethod
    def _desktop_dirs() -> list[Path]:
        return [
            Path("/usr/share/applications"),
            Path.home() / ".local/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
            Path.home() / ".local/share/flatpak/exports/share/applications",
        ]

    def _load_apps(self) -> list[AppEntry]:
        entries: list[AppEntry] = []
        for directory in self._desktop_dirs():
            if not directory.exists():
                continue
            for path in directory.glob("*.desktop"):
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                try:
                    parser.read(path, encoding="utf-8")
                except configparser.Error:
                    continue
                if "Desktop Entry" not in parser:
                    continue
                data = parser["Desktop Entry"]
                if data.get("Type") != "Application":
                    continue
                if data.get("NoDisplay", "false").lower() == "true":
                    continue
                name = data.get("Name")
                exec_cmd = data.get("Exec")
                if not name or not exec_cmd:
                    continue
                keywords = []
                if "Keywords" in data:
                    keywords = [kw.strip() for kw in data["Keywords"].split(";") if kw.strip()]
                entries.append(
                    AppEntry(
                        name=name,
                        desktop_id=path.stem,
                        exec_cmd=exec_cmd,
                        keywords=keywords,
                    )
                )
        return entries

    def search(self, query: str, limit: int = 10) -> list[AppEntry]:
        normalized = query.lower().strip()
        if not normalized:
            return []
        scored: list[tuple[int, AppEntry]] = []
        for app in self._apps:
            haystack = " ".join([app.name, app.desktop_id, *app.keywords]).lower()
            if normalized in haystack:
                score = 100 - len(app.name)
                scored.append((score, app))
        if not scored:
            names = [app.name for app in self._apps]
            matches = difflib.get_close_matches(query, names, n=limit, cutoff=0.6)
            for app in self._apps:
                if app.name in matches:
                    scored.append((50, app))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [app for _, app in scored[:limit]]


def _sanitize_exec(exec_cmd: str) -> list[str]:
    tokens = shlex.split(exec_cmd)
    cleaned = [token for token in tokens if "%" not in token]
    return cleaned or tokens


@dataclass
class SearchAppsTool(BaseTool):
    app_index: AppIndex
    metadata: ToolMetadata = ToolMetadata(
        name="search_apps",
        description="Search installed desktop applications by name.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        query = arguments.get("query")
        limit = int(arguments.get("limit", 10))
        if not isinstance(query, str):
            return {"success": False, "error": "Query is required."}
        matches = self.app_index.search(query, limit=limit)
        output = [
            {"name": app.name, "desktop_id": app.desktop_id, "exec": app.exec_cmd}
            for app in matches
        ]
        return {"success": True, "output": output}


@dataclass
class OpenAppTool(BaseTool):
    config: ToolConfig
    app_index: AppIndex
    metadata: ToolMetadata = ToolMetadata(
        name="open_app",
        description="Open a desktop application by name.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        name = arguments.get("name")
        if not isinstance(name, str):
            return {"success": False, "error": "Missing application name."}

        commands = self.config.app_commands.get(name.lower(), [])
        for command in commands:
            if shutil.which(command):
                subprocess.Popen(
                    [command],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"success": True, "output": f"{name} launched."}

        if shutil.which(name):
            subprocess.Popen(
                [name],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"success": True, "output": f"{name} launched."}

        match = self.app_index.search(name, limit=1)
        if match:
            app = match[0]
            if shutil.which("gtk-launch"):
                subprocess.Popen(
                    ["gtk-launch", app.desktop_id],
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"success": True, "output": f"{app.name} launched."}
            cmd = _sanitize_exec(app.exec_cmd)
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"success": True, "output": f"{app.name} launched."}

        return {
            "success": False,
            "error": f"No application found for {name}.",
        }


def get_tools(config: ToolConfig) -> list[BaseTool]:
    index = AppIndex()
    return [
        SearchAppsTool(app_index=index),
        OpenAppTool(config=config, app_index=index),
    ]
