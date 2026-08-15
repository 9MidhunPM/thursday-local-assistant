from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Project root (…/ThursdayV3), used to resolve relative paths in config.json
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SYSTEM_PROMPT = """You are Thursday, a highly capable local-first AI assistant (JARVIS-style).
The user's name is {user_name}.

Core principles:
1. Be sharp, direct, and useful — no fluff, no filler apologies.
2. Prefer action over speculation. When the user asks you to DO something, call the right tool.
3. Never invent tool results. Only claim an action happened after a successful tool call.
4. After tools succeed, give a concise confirmation of what was done (and key results).
5. Use multiple independent tools in one turn when that is faster.
6. For ambiguous paths/names, try case-insensitive and fuzzy matching via tools.
7. Prefer real-time tools for live data (time, system, web, music) over memory alone.
8. Remember durable personal facts via memory tools when the user states preferences.
9. Never dump raw JSON or internal reasoning to the user.
10. If a tool fails, explain briefly and try a sensible alternative when available.

Tool habits:
- Music: use Spotify tools directly (do not open a browser to play songs).
- Folders the user wants to see/open: use open_path; after a numbered search choice use reveal_path.
- File search: use find_file_system / search_files rather than guessing paths.
- Shell: use run_terminal_command when it is the most direct reliable action; execute instead of
  merely printing a command the user asked to run.
- Project building: use codex_orchestrate for software implementation, debugging, or project setup
  the user asks Codex to handle. For a new project, first ask concise questions for any material
  product, stack, or design details that are missing; never open ChatGPT or another app. Use the
  selected Codex model when the user provides one. Keep work in Thursday's dedicated Codex workspace.
- Email: use gmail_compose for drafts. Ask for a missing recipient, generate a useful subject/body,
  and never claim a draft was sent. Use summarize_inbox for the latest-20 inbox brief.
- Calendar: read with calendar_agenda before an update; create/update tools always preview and
  request confirmation before saving.
- Instagram: use watch_reels to start safe 15-second scrolling and stop_watching_reels to stop it.
- Website reviews: use analyze_website for visual/design questions about a URL. Use
  search_and_fetch for researched answers and open_google_search only when the user asks to see
  Google results in the browser.
- Memory delete: use the dedicated delete/forget tools when asked to forget something.
"""


def _resolve_path(value: str) -> str:
    """Resolve a config path against the project root when it is relative."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def _resolve_base_url(config_url: str, provider: str) -> str:
    """Resolve provider-specific model endpoints without leaking local overrides to cloud mode."""
    if os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL"):
        return (os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    host = os.getenv("LLAMA_HOST") if provider == "local" else None
    port = os.getenv("LLAMA_PORT") if provider == "local" else None
    if host or port:
        host = host or "127.0.0.1"
        port = port or "8080"
        return f"http://{host}:{port}"
    return config_url


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> list[str] | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return [_resolve_path(p.strip()) for p in raw.split(",") if p.strip()]


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    temperature: float
    max_tokens: int | None
    request_timeout_sec: int
    json_retries: int
    response_format: str | None
    provider: str = "local"
    api_key: str | None = None
    context_budget_tokens: int = 7000


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
    # Empty = all tools. Non-empty = only these groups (plus always-on core).
    enabled_groups: list[str] = field(default_factory=list)
    unrestricted_paths: bool = False


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
    user_name: str = "User"
    smart_tool_filter: bool = True
    web_confirm_timeout_sec: int = 60


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
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("YAML config requested but PyYAML is not installed.") from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("YAML config must be a mapping at the top level.")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _render_prompt(template: str, user_name: str) -> str:
    try:
        return template.format(user_name=user_name)
    except (KeyError, ValueError, IndexError):
        # If the prompt has other braces (JSON examples), leave it as-is after a simple replace.
        return template.replace("{user_name}", user_name)


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = _load_yaml(path)
    else:
        raw = _load_json(path)

    model = raw.get("model", {})
    tools = raw.get("tools", {})
    terminal = tools.get("terminal", {})
    memory = raw.get("memory", {})
    logging_cfg = raw.get("logging", {})
    voice_cfg = raw.get("voice", {})
    agent_cfg = raw.get("agent", {})

    # --- User identity ---
    user_name = (
        os.getenv("THURSDAY_USER_NAME")
        or os.getenv("USER_NAME")
        or agent_cfg.get("user_name")
        or "User"
    )

    # --- Model / provider ---
    provider = (
        os.getenv("LLM_PROVIDER")
        or model.get("provider")
        or "local"
    ).strip().lower()
    if provider in {"llama", "llamacpp", "llama.cpp"}:
        provider = "local"

    config_base = model.get("base_url", "http://127.0.0.1:8080")
    base_url = _resolve_base_url(config_base, provider)
    model_name = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or model.get("model", "local-model")

    context_budget = int(
        os.getenv("LLM_CONTEXT_BUDGET")
        or model.get("context_budget_tokens", 7000)
    )
    # Cloud models usually have larger context — default higher budget.
    if provider != "local" and not model.get("context_budget_tokens") and not os.getenv(
        "LLM_CONTEXT_BUDGET"
    ):
        context_budget = 100_000

    max_tokens = model.get("max_tokens", 1024)
    if os.getenv("LLM_MAX_TOKENS"):
        max_tokens = int(os.getenv("LLM_MAX_TOKENS", "1024"))

    timeout_sec = int(os.getenv("LLM_TIMEOUT_SEC") or model.get("request_timeout_sec", 90))

    # --- Paths / sandbox ---
    default_read = tools.get("read_roots", tools.get("allowed_roots", ["~", "."]))
    default_write = tools.get("write_roots", tools.get("allowed_roots", ["."]))
    # Empty list in config historically meant unrestricted — treat as home+project unless power mode.
    if default_read == []:
        default_read = ["~", "."]
    if default_write == []:
        default_write = ["."]

    unrestricted = bool(
        _env_bool("THURSDAY_UNRESTRICTED_PATHS", tools.get("unrestricted_paths", False))
    )
    if unrestricted:
        read_roots: list[str] = []
        write_roots: list[str] = []
    else:
        read_roots = _env_list("THURSDAY_READ_ROOTS") or [
            _resolve_path(p) for p in default_read
        ]
        write_roots = _env_list("THURSDAY_WRITE_ROOTS") or [
            _resolve_path(p) for p in default_write
        ]

    allow_shell = _env_bool("THURSDAY_ALLOW_SHELL", bool(terminal.get("allow_shell", False)))
    assert allow_shell is not None

    # --- System prompt ---
    prompt_template = (
        os.getenv("THURSDAY_SYSTEM_PROMPT")
        or agent_cfg.get("system_prompt")
        or DEFAULT_SYSTEM_PROMPT
    )
    system_prompt = _render_prompt(prompt_template, user_name)

    max_tool_steps = int(
        os.getenv("THURSDAY_MAX_TOOL_STEPS") or agent_cfg.get("max_tool_steps", 6)
    )
    # Smarter multi-step by default for API providers
    if provider != "local" and not agent_cfg.get("max_tool_steps") and not os.getenv(
        "THURSDAY_MAX_TOOL_STEPS"
    ):
        max_tool_steps = 8

    enabled_groups = _split_csv(os.getenv("THURSDAY_TOOL_GROUPS")) or list(
        tools.get("enabled_groups", [])
    )

    return AppConfig(
        model=ModelConfig(
            base_url=base_url,
            model=model_name,
            temperature=float(os.getenv("LLM_TEMPERATURE") or model.get("temperature", 0.2)),
            max_tokens=max_tokens,
            request_timeout_sec=timeout_sec,
            json_retries=int(model.get("json_retries", 2)),
            response_format=model.get("response_format"),
            provider=provider,
            api_key=os.getenv("LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or os.getenv("TOGETHER_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("MISTRAL_API_KEY")
            or model.get("api_key"),
            context_budget_tokens=context_budget,
        ),
        tools=ToolConfig(
            read_roots=read_roots,
            write_roots=write_roots,
            app_commands={
                key: list(value) for key, value in tools.get("app_commands", {}).items()
            },
            terminal=TerminalSafetyConfig(
                allow_shell=allow_shell,
                whitelist_commands=list(terminal.get("whitelist_commands", [])),
                blacklist_patterns=list(terminal.get("blacklist_patterns", [])),
                confirm_patterns=list(terminal.get("confirm_patterns", [])),
                timeout_sec=int(terminal.get("timeout_sec", 15)),
            ),
            enabled_groups=enabled_groups,
            unrestricted_paths=unrestricted,
        ),
        memory=MemoryConfig(
            db_path=_resolve_path(memory.get("db_path", "assistant/database/memory.db"))
        ),
        logging=LoggingConfig(
            directory=_resolve_path(logging_cfg.get("directory", "assistant/logs")),
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
            max_tool_steps=max_tool_steps,
            system_prompt=system_prompt,
            stream_responses=bool(agent_cfg.get("stream_responses", True)),
            max_parallel_tools=int(agent_cfg.get("max_parallel_tools", 4)),
            auto_extract=bool(agent_cfg.get("auto_extract", True)),
            extract_max_tokens=int(agent_cfg.get("extract_max_tokens", 256)),
            user_name=str(user_name),
            smart_tool_filter=bool(
                _env_bool(
                    "THURSDAY_SMART_TOOLS",
                    agent_cfg.get("smart_tool_filter", True),
                )
            ),
            web_confirm_timeout_sec=int(agent_cfg.get("web_confirm_timeout_sec", 60)),
        ),
    )
