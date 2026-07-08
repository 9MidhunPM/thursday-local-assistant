from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Project root (…/ThursdayV3), used to resolve relative paths in config.json
# so the config is portable across machines/checkout locations.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_path(value: str) -> str:
    """Resolve a config path against the project root when it is relative.

    Absolute paths and ``~`` are honoured as-is; relative paths are anchored to
    the project root so config.json never needs to hardcode an absolute path.
    """
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def _resolve_base_url(config_url: str) -> str:
    """Allow LLAMA_HOST / LLAMA_PORT env vars to override the model base URL."""
    host = os.getenv("LLAMA_HOST")
    port = os.getenv("LLAMA_PORT")
    if host or port:
        host = host or "127.0.0.1"
        port = port or "8080"
        return f"http://{host}:{port}"
    return config_url


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    temperature: float
    max_tokens: int | None
    request_timeout_sec: int
    json_retries: int
    response_format: str | None


@dataclass(frozen=True)
class TerminalSafetyConfig:
    allow_shell: bool
    whitelist_commands: list[str]
    blacklist_patterns: list[str]
    confirm_patterns: list[str]
    timeout_sec: int


@dataclass(frozen=True)
class ToolConfig:
    read_roots: list[str]
    write_roots: list[str]
    app_commands: dict[str, list[str]]
    terminal: TerminalSafetyConfig


@dataclass(frozen=True)
class MemoryConfig:
    db_path: str


@dataclass(frozen=True)
class LoggingConfig:
    directory: str
    level: str


@dataclass(frozen=True)
class VoiceConfig:
    tts_enabled: bool
    tts_voice: str
    tts_rate: str
    stt_enabled: bool
    stt_recognizer: str
    stt_language: str


@dataclass(frozen=True)
class AgentConfig:
    max_tool_steps: int
    system_prompt: str
    stream_responses: bool
    max_parallel_tools: int
    auto_extract: bool
    extract_max_tokens: int


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig
    tools: ToolConfig
    memory: MemoryConfig
    logging: LoggingConfig
    voice: VoiceConfig
    agent: AgentConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "YAML config requested but PyYAML is not installed."
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("YAML config must be a mapping at the top level.")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = _load_yaml(path)
    else:
        raw = _load_json(path)

    model = raw["model"]
    tools = raw["tools"]
    terminal = tools["terminal"]
    memory = raw["memory"]
    logging_cfg = raw["logging"]
    voice_cfg = raw.get("voice", {})
    agent_cfg = raw["agent"]

    return AppConfig(
        model=ModelConfig(
            base_url=_resolve_base_url(model["base_url"]),
            model=model["model"],
            temperature=float(model.get("temperature", 0.2)),
            max_tokens=model.get("max_tokens"),
            request_timeout_sec=int(model.get("request_timeout_sec", 60)),
            json_retries=int(model.get("json_retries", 2)),
            response_format=model.get("response_format"),
        ),
        tools=ToolConfig(
            read_roots=[
                _resolve_path(p)
                for p in tools.get("read_roots", tools.get("allowed_roots", []))
            ],
            write_roots=[
                _resolve_path(p)
                for p in tools.get("write_roots", tools.get("allowed_roots", []))
            ],
            app_commands={
                key: list(value) for key, value in tools.get("app_commands", {}).items()
            },
            terminal=TerminalSafetyConfig(
                allow_shell=bool(terminal.get("allow_shell", False)),
                whitelist_commands=list(terminal.get("whitelist_commands", [])),
                blacklist_patterns=list(terminal.get("blacklist_patterns", [])),
                confirm_patterns=list(terminal.get("confirm_patterns", [])),
                timeout_sec=int(terminal.get("timeout_sec", 15)),
            ),
        ),
        memory=MemoryConfig(db_path=_resolve_path(memory["db_path"])),
        logging=LoggingConfig(
            directory=_resolve_path(logging_cfg["directory"]),
            level=logging_cfg.get("level", "INFO"),
        ),
        voice=VoiceConfig(
            tts_enabled=bool(voice_cfg.get("tts_enabled", True)),
            tts_voice=str(voice_cfg.get("tts_voice", "en-US-EmmaMultilingualNeural")),
            tts_rate=str(voice_cfg.get("tts_rate", "+25%")),
            stt_enabled=bool(voice_cfg.get("stt_enabled", True)),
            stt_recognizer=str(voice_cfg.get("stt_recognizer", "google")),
            stt_language=str(voice_cfg.get("stt_language", "en-US")),
        ),
        agent=AgentConfig(
            max_tool_steps=int(agent_cfg.get("max_tool_steps", 4)),
            system_prompt=agent_cfg["system_prompt"],
            stream_responses=bool(agent_cfg.get("stream_responses", True)),
            max_parallel_tools=int(agent_cfg.get("max_parallel_tools", 4)),
            auto_extract=bool(agent_cfg.get("auto_extract", True)),
            extract_max_tokens=int(agent_cfg.get("extract_max_tokens", 256)),
        ),
    )
