from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.config.loader import ToolConfig
from assistant.tools.base import BaseTool, ToolMetadata


def _default_directory_path(path: str | None) -> str:
    if not isinstance(path, str) or not path.strip():
        return str(Path.home())
    return path


def _resolve_case_insensitive(path: Path) -> Path:
    if not path.is_absolute():
        return path
    current = Path(path.anchor)
    for part in path.parts[1:]:
        direct = current / part
        if direct.exists():
            current = direct
            continue
        try:
            children = list(current.iterdir())
        except OSError:
            current = direct
            continue
        match = next((child for child in children if child.name.lower() == part.lower()), None)
        current = match or direct
    return current


def _candidate_paths(path: str) -> list[Path]:
    raw = Path(path).expanduser()
    home = Path.home()
    cwd = Path.cwd()
    candidates: list[Path] = []

    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(cwd / raw)
        candidates.append(home / raw)

        # Search upwards in parent directories of cwd
        parts = raw.parts
        if parts:
            first_part = parts[0]
            for parent in [cwd] + list(cwd.parents):
                if parent.name.lower() == first_part.lower():
                    candidates.append(parent / Path(*parts[1:]))
                if parent.parent:
                    try:
                        for sibling in parent.parent.iterdir():
                            if sibling.is_dir() and sibling.name.lower() == first_part.lower():
                                candidates.append(sibling / Path(*parts[1:]))
                    except OSError:
                        pass

        # Substring / fuzzy matching in CWD, siblings, and Home
        if len(raw.parts) == 1:
            raw_name_lower = raw.name.lower()
            try:
                for child in cwd.iterdir():
                    if raw_name_lower in child.name.lower():
                        candidates.append(child)
            except OSError:
                pass
            try:
                for child in cwd.parent.iterdir():
                    if raw_name_lower in child.name.lower():
                        candidates.append(child)
            except OSError:
                pass
            try:
                for child in home.iterdir():
                    if raw_name_lower in child.name.lower():
                        candidates.append(child)
                    if child.is_dir() and child.name.lower() in ("coading", "coding", "documents", "projects"):
                        for subchild in child.iterdir():
                            if raw_name_lower in subchild.name.lower():
                                candidates.append(subchild)
            except OSError:
                pass

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve(strict=False))
        except OSError:
            key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _resolve_existing_path(path: str) -> Path | None:
    for candidate in _candidate_paths(path):
        try:
            resolved = _resolve_case_insensitive(candidate.resolve(strict=False))
        except OSError:
            continue
        if resolved.exists():
            return resolved
    return None


def _resolve_path(path: str, allowed_roots: list[str]) -> Path:
    target = _resolve_existing_path(path) or Path(path).expanduser().resolve()
    if not allowed_roots:
        return target
    for root in allowed_roots:
        root_path = Path(root).expanduser().resolve()
        if root_path in target.parents or target == root_path:
            return target
    raise ValueError("Path is outside allowed roots.")


@dataclass
class ReadFileTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="read_file",
        description="Read a text file from disk.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = arguments.get("path")
        if not isinstance(path, str):
            return {"success": False, "error": "Path is required."}
        target = _resolve_path(path, self.config.read_roots)
        if not target.exists():
            return {"success": False, "error": "File does not exist."}
        if target.stat().st_size > 10 * 1024 * 1024:
            return {"success": False, "error": "File is too large (> 10MB) to read into context."}
            
        try:
            with open(target, 'rb') as f:
                header = f.read(1024)
                if b'\x00' in header:
                    return {"success": False, "error": "Cannot read binary files. Use open_path to open it visually instead."}
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"success": False, "error": "File is not valid UTF-8 text. Use open_path to open it visually instead."}
        return {"success": True, "output": content}


@dataclass
class WriteFileTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="write_file",
        description="Write text content to a file on disk.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["path", "content"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = arguments.get("path")
        content = arguments.get("content")
        overwrite = bool(arguments.get("overwrite", False))
        if not isinstance(path, str) or not isinstance(content, str):
            return {"success": False, "error": "Path and content are required."}
        target = _resolve_path(path, self.config.write_roots)
        if target.exists() and not overwrite:
            return {"success": False, "error": "File exists and overwrite is false."}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"success": True, "output": "File written."}





@dataclass
class SearchFilesTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="search_files",
        description="Search for files recursively by name pattern and optional content.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "contains": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["pattern"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = _default_directory_path(arguments.get("path"))
        pattern = arguments.get("pattern")
        contains = arguments.get("contains")
        max_results = int(arguments.get("max_results", 50))
        if not isinstance(pattern, str):
            return {"success": False, "error": "Pattern is required."}
        target = _resolve_path(path, self.config.read_roots)
        if not target.exists() or not target.is_dir():
            return {"success": False, "error": "Directory not found."}
        results: list[str] = []
        for root, _, files in os.walk(target):
            for file_name in files:
                # Use case-insensitive matching, and auto-wildcard if no wildcards are provided
                search_pattern = pattern if ('*' in pattern or '?' in pattern) else f"*{pattern}*"
                if not fnmatch.fnmatch(file_name.lower(), search_pattern.lower()):
                    continue
                file_path = Path(root) / file_name
                if isinstance(contains, str):
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if contains not in text:
                        continue
                results.append(str(file_path))
                if len(results) >= max_results:
                    return {"success": True, "output": results}
        return {"success": True, "output": results}


@dataclass
class FindFoldersTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="find_folders",
        description="Find folders recursively by name pattern.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["pattern"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = _default_directory_path(arguments.get("path"))
        pattern = arguments.get("pattern")
        max_results = int(arguments.get("max_results", 50))
        if not isinstance(pattern, str):
            return {"success": False, "error": "Pattern is required."}
        target = _resolve_path(path, self.config.read_roots)
        if not target.exists() or not target.is_dir():
            return {"success": False, "error": "Directory not found."}
        results: list[str] = []
        for root, dirs, _ in os.walk(target):
            for directory_name in dirs:
                if not fnmatch.fnmatch(directory_name, pattern):
                    continue
                folder_path = Path(root) / directory_name
                results.append(str(folder_path))
                if len(results) >= max_results:
                    return {"success": True, "output": results}
        return {"success": True, "output": results}


@dataclass
class MoveFileTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="move_file",
        description="Move or rename a file or directory.",
        parameters={
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "The path to the file/directory to move."},
                "destination": {"type": "string", "description": "The destination path or new name."}
            },
            "required": ["source", "destination"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        source = arguments.get("source")
        destination = arguments.get("destination")
        if not isinstance(source, str) or not isinstance(destination, str):
            return {"success": False, "error": "Source and destination are required."}
            
        src_path = _resolve_path(source, self.config.write_roots)
        dst_path = _resolve_path(destination, self.config.write_roots)
        
        if not src_path.exists():
            return {"success": False, "error": f"Source not found: {source}"}
            
        try:
            shutil.move(str(src_path), str(dst_path))
            return {"success": True, "output": f"Moved {source} to {destination}."}
        except Exception as e:
            return {"success": False, "error": f"Failed to move file: {e}"}


@dataclass
class DeleteFileTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="delete_file",
        description="Delete a file or directory permanently.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path to the file/directory to delete."}
            },
            "required": ["path"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = arguments.get("path")
        if not isinstance(path, str):
            return {"success": False, "error": "Path is required."}
            
        target = _resolve_path(path, self.config.write_roots)
        
        if not target.exists():
            return {"success": False, "error": f"Path not found: {path}"}
            
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return {"success": True, "output": f"Deleted {path}."}
        except Exception as e:
            return {"success": False, "error": f"Failed to delete file: {e}"}


@dataclass
class OpenFileOrFolderTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="open_path",
        description="Open a file or folder visually in the user's default application (e.g. open a PDF, image, or folder).",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = _default_directory_path(arguments.get("path"))
        target = _resolve_path(path, self.config.read_roots)
        if not target.exists():
            return {"success": False, "error": "Path not found."}
        
        if target.is_dir() and shutil.which("hyprctl") and shutil.which("thunar"):
            subprocess.run(
                ["hyprctl", "dispatch", "exec", f"thunar {target}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"success": True, "output": f"Opened {target} in Thunar."}
        elif shutil.which("xdg-open"):
            subprocess.Popen(
                ["xdg-open", str(target)],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"success": True, "output": f"Opened {target}."}
        return {"success": False, "error": "No default application found (xdg-open missing)."}


@dataclass
class FindFileSystemTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="find_file_system",
        description="Search the entire system for a specific file or folder. Handles typos via fuzzy matching.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Core keywords of the file name (e.g. 'resume' instead of 'my resume')."},
                "max_results": {"type": "integer"}
            },
            "required": ["filename"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        filename = arguments.get("filename")
        max_results = int(arguments.get("max_results", 20))
        if not isinstance(filename, str):
            return {"success": False, "error": "Filename is required."}
            
        # Generalize the search by removing common stop words
        stop_words = {"my", "the", "a", "an", "find", "search", "for", "file", "folder", "where", "is"}
        words = [w for w in filename.lower().split() if w not in stop_words]
        if not words:
            words = [filename.strip()]
            
        # Create a wildcard pattern from the words
        search_pattern = "*" + "*".join(words) + "*"
            
        # Try `locate` first if it exists, it's much faster
        if shutil.which("locate"):
            res = subprocess.run(["locate", "-i", "--limit", str(max_results), search_pattern], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                lines = res.stdout.strip().splitlines()
                return {"success": True, "output": lines}
                
        # Fallback to `find /` with reasonable exclusions
        find_cmd = [
            "find", "/",
            "-path", "/proc", "-prune", "-o",
            "-path", "/sys", "-prune", "-o",
            "-path", "/dev", "-prune", "-o",
            "-path", "/run", "-prune", "-o",
            "-path", "/snap", "-prune", "-o",
            "-iname", search_pattern, "-print"
        ]
        
        try:
            # We use timeout because system-wide find can be slow
            res = subprocess.run(find_cmd, capture_output=True, text=True, timeout=10)
            lines = res.stdout.strip().splitlines()
            return {"success": True, "output": lines[:max_results] if lines else []}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "System-wide search timed out."}


def get_tools(config: ToolConfig) -> list[BaseTool]:
    return [
        ReadFileTool(config=config),
        WriteFileTool(config=config),
        SearchFilesTool(config=config),
        FindFoldersTool(config=config),
        MoveFileTool(config=config),
        DeleteFileTool(config=config),
        OpenFileOrFolderTool(config=config),
        FindFileSystemTool(config=config),
    ]
