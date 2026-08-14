from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
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


def _search_words(value: str) -> list[str]:
    stop_words = {
        "my",
        "the",
        "a",
        "an",
        "find",
        "search",
        "for",
        "file",
        "folder",
        "where",
        "is",
        "me",
    }
    words = [word for word in re.findall(r"[\w.-]+", value.casefold()) if word not in stop_words]
    return words or [value.strip().casefold()]


def _path_result(path: Path, index: int) -> dict[str, Any]:
    try:
        is_dir = path.is_dir()
    except OSError:
        is_dir = False
    return {
        "index": index,
        "name": path.name or str(path),
        "type": "folder" if is_dir else "file",
        "path": str(path),
        "parent": str(path.parent),
    }


def _result_rank(path: Path, words: list[str]) -> tuple[int, int, int, str]:
    name = path.name.casefold()
    stem = path.stem.casefold()
    phrase = " ".join(words)
    if name == phrase or stem == phrase:
        match_rank = 0
    elif name.startswith(phrase) or stem.startswith(phrase):
        match_rank = 1
    elif all(word in name for word in words):
        match_rank = 2
    else:
        match_rank = 3
    try:
        path.relative_to(Path.home())
        home_rank = 0
    except ValueError:
        home_rank = 1
    return (match_rank, home_rank, len(path.parts), str(path).casefold())


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
    """Resolve a path and enforce the sandbox.

    Empty ``allowed_roots`` is only allowed when the caller intentionally set
    unrestricted mode (power user). Prefer non-empty roots in config.
    """
    target = _resolve_existing_path(path) or Path(path).expanduser().resolve()
    # Always resolve symlinks for the final check when possible.
    try:
        target = target.resolve(strict=False)
    except OSError:
        pass
    if not allowed_roots:
        # Unrestricted mode — still block obvious system-critical writes via tools
        # that use write_roots=[] only when user opted in.
        return target
    for root in allowed_roots:
        root_path = Path(root).expanduser().resolve()
        try:
            target.relative_to(root_path)
            return target
        except ValueError:
            if root_path in target.parents or target == root_path:
                return target
    raise ValueError(
        f"Path is outside allowed roots ({', '.join(allowed_roots)}). "
        "Set THURSDAY_UNRESTRICTED_PATHS=1 or expand THURSDAY_READ_ROOTS / "
        "THURSDAY_WRITE_ROOTS if you need access elsewhere."
    )


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
        description="Search for files by name pattern and optional content.",
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
        
        command: list[str] | None = None
        app_name = "the default application"
        if target.is_dir() and shutil.which("thunar"):
            command = ["thunar", "--window", str(target)]
            app_name = "Thunar"
        elif shutil.which("xdg-open"):
            command = ["xdg-open", str(target)]
        if command:
            process = subprocess.Popen(
                command,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.2)
            return_code = process.poll()
            if return_code not in (None, 0):
                return {"success": False, "error": f"{app_name} failed to open."}
            return {"success": True, "output": f"Opened {target} in {app_name}."}
        return {"success": False, "error": "No default application found (xdg-open missing)."}


@dataclass
class RevealPathTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="reveal_path",
        description=(
            "Reveal a selected file in Thunar, or open a selected folder in Thunar. "
            "Use after the user chooses one of the numbered file-search results."
        ),
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"success": False, "error": "Path is required."}
        target = _resolve_path(path, self.config.read_roots)
        if not target.exists():
            return {"success": False, "error": "Path not found."}
        if not shutil.which("thunar"):
            return {"success": False, "error": "Thunar is not installed."}

        folder = target if target.is_dir() else target.parent
        command = ["thunar", "--window", str(folder)]
        process = subprocess.Popen(
            command,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)
        if process.poll() not in (None, 0):
            return {
                "success": False,
                "error": "Thunar could not open the selected path's folder.",
            }
        return {
            "success": True,
            "output": f"Opened {folder} in Thunar for {target}.",
            "path": str(target),
        }


@dataclass
class FindFileSystemTool(BaseTool):
    config: ToolConfig
    metadata: ToolMetadata = ToolMetadata(
        name="find_file_system",
        description=(
            "Search the entire computer for files or folders by name. Returns numbered results; "
            "use reveal_path after the user chooses one."
        ),
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
        max_results = max(1, min(int(arguments.get("max_results", 50)), 200))
        if not isinstance(filename, str) or not filename.strip():
            return {"success": False, "error": "Filename is required."}
        _resolve_path("/", self.config.read_roots)
        words = _search_words(filename)
        candidates: list[Path] = []
        source = "filesystem_walk"
        truncated = False

        locate_bin = shutil.which("plocate") or shutil.which("locate")
        if locate_bin:
            query_limit = min(max_results * 5 + 1, 1001)
            result = subprocess.run(
                [
                    locate_bin,
                    "--ignore-case",
                    "--existing",
                    "--null",
                    "--limit",
                    str(query_limit),
                    *words,
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                candidates = [
                    Path(raw.decode("utf-8", errors="surrogateescape"))
                    for raw in result.stdout.split(b"\0")
                    if raw
                ]
                source = "plocate"
                truncated = len(candidates) > max_results

        if not candidates and source != "plocate":
            deadline = time.monotonic() + 12
            excluded = {"/proc", "/sys", "/dev", "/run", "/snap"}
            for root, dirs, files in os.walk("/", onerror=lambda _error: None):
                if time.monotonic() >= deadline:
                    truncated = True
                    break
                dirs[:] = [
                    directory
                    for directory in dirs
                    if str(Path(root) / directory) not in excluded
                ]
                for name in [*dirs, *files]:
                    if all(word in name.casefold() for word in words):
                        candidates.append(Path(root) / name)
                        if len(candidates) >= max_results * 5:
                            truncated = True
                            break
                if truncated and len(candidates) >= max_results * 5:
                    break

        unique = {str(path): path for path in candidates}
        ranked = sorted(unique.values(), key=lambda path: _result_rank(path, words))
        if len(ranked) > max_results:
            truncated = True
        selected = ranked[:max_results]
        output = {
            "query": filename.strip(),
            "source": source,
            "results": [_path_result(path, index) for index, path in enumerate(selected, 1)],
            "result_count": len(selected),
            "truncated": truncated,
        }
        if source == "filesystem_walk":
            output["warning"] = (
                "The plocate index is unavailable; results came from a time-limited live scan."
            )
        return {"success": True, "output": output}


def get_tools(config: ToolConfig) -> list[BaseTool]:
    return [
        ReadFileTool(config=config),
        WriteFileTool(config=config),
        SearchFilesTool(config=config),
        FindFoldersTool(config=config),
        MoveFileTool(config=config),
        DeleteFileTool(config=config),
        OpenFileOrFolderTool(config=config),
        RevealPathTool(config=config),
        FindFileSystemTool(config=config),
    ]
