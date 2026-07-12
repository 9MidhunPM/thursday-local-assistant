from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .agent.agent import Agent, AgentConfig
from .config import AppConfig, load_config
from .logging_utils import Loggers, setup_logging
from .llm.client import LlamaCppClient, build_llm_client
from .memory.conversation_store import ConversationStore
from .memory.long_term import LongTermMemory
from .memory.session import SessionMemory
from .tools.registry import ToolRegistry
from .voice.stt import SpeechToText
from .voice.tts import TextToSpeech


def default_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "config.json"


def _build_tts(config: AppConfig) -> TextToSpeech | None:
    if not config.voice.tts_enabled:
        return None
    try:
        from assistant.voice.tts import EdgeTTS

        return EdgeTTS(
            voice=config.voice.tts_voice,
            rate=config.voice.tts_rate,
        )
    except (FileNotFoundError, ImportError) as e:
        import logging

        logging.getLogger("assistant").warning("TTS unavailable: %s", e)
        return None


def _build_stt(config: AppConfig) -> SpeechToText | None:
    if not config.voice.stt_enabled:
        return None
    try:
        from assistant.voice.stt import SpeechRecognitionSTT

        return SpeechRecognitionSTT(
            language=config.voice.stt_language,
            recognizer=config.voice.stt_recognizer,
        )
    except (ImportError, OSError) as e:
        import logging

        logging.getLogger("assistant").warning("STT unavailable: %s", e)
        return None


@dataclass(frozen=True)
class AssistantRuntime:
    agent: Agent
    config: AppConfig
    loggers: Loggers
    conversation_store: ConversationStore
    llm: LlamaCppClient
    tts: TextToSpeech | None = None
    stt: SpeechToText | None = None

    def shutdown(self) -> None:
        """Gracefully shutdown the runtime components."""
        if self.llm:
            self.llm.close()
        if self.tts:
            self.tts.stop()


def build_runtime(config_path: Path | None = None) -> AssistantRuntime:
    # Load environment variables from .env file
    load_dotenv()

    resolved_config_path = config_path or default_config_path()
    config = load_config(resolved_config_path)
    loggers = setup_logging(Path(config.logging.directory), config.logging.level)

    llm = build_llm_client(
        base_url=config.model.base_url,
        model=config.model.model,
        temperature=config.model.temperature,
        max_tokens=config.model.max_tokens,
        timeout_sec=config.model.request_timeout_sec,
        response_format=config.model.response_format,
        provider=config.model.provider,
        api_key=config.model.api_key,
    )

    # Session budget scales with model context
    session_tokens = min(4000, max(600, config.model.context_budget_tokens // 8))

    tool_registry = ToolRegistry.load_builtin(config.tools)
    memory = LongTermMemory(Path(config.memory.db_path))
    conversation_store = ConversationStore(Path(config.memory.db_path))
    short_term = SessionMemory(conversation_store, max_tokens=session_tokens)
    agent = Agent(
        llm=llm,
        tool_registry=tool_registry,
        memory=memory,
        short_term=short_term,
        loggers=loggers,
        config=AgentConfig(
            max_tool_steps=config.agent.max_tool_steps,
            system_prompt=config.agent.system_prompt,
            json_retries=config.model.json_retries,
            stream_responses=config.agent.stream_responses,
            max_parallel_tools=config.agent.max_parallel_tools,
            auto_extract=config.agent.auto_extract,
            extract_max_tokens=config.agent.extract_max_tokens,
            context_budget_tokens=config.model.context_budget_tokens,
            smart_tool_filter=config.agent.smart_tool_filter,
            enabled_tool_groups=tuple(config.tools.enabled_groups),
            web_confirm_timeout_sec=config.agent.web_confirm_timeout_sec,
            user_name=config.agent.user_name,
        ),
    )
    tts = _build_tts(config)
    stt = _build_stt(config)

    mode = "local" if llm.is_local else f"api:{llm.provider}"
    loggers.model.info(
        "runtime_ready provider=%s model=%s base_url=%s mode=%s tools=%s",
        llm.provider,
        llm.model,
        llm.base_url,
        mode,
        len(list(tool_registry.tools())),
    )

    return AssistantRuntime(
        agent=agent,
        config=config,
        loggers=loggers,
        conversation_store=conversation_store,
        llm=llm,
        tts=tts,
        stt=stt,
    )
