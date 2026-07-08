from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .agent.agent import Agent, AgentConfig
from .config import AppConfig, load_config
from .logging_utils import Loggers, setup_logging
from .llm.client import LlamaCppClient
from .memory.conversation_store import ConversationStore
from .memory.long_term import LongTermMemory
from .memory.session import SessionMemory
from .memory.short_term import ShortTermMemory
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
        logging.getLogger("assistant").warning(f"TTS unavailable: {e}")
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
        logging.getLogger("assistant").warning(f"STT unavailable: {e}")
        return None


@dataclass(frozen=True)
class AssistantRuntime:
    agent: Agent
    config: AppConfig
    loggers: Loggers
    conversation_store: ConversationStore
    tts: TextToSpeech | None = None
    stt: SpeechToText | None = None

    def shutdown(self) -> None:
        """Gracefully shutdown the runtime components."""
        if hasattr(self.agent, '_llm') and self.agent._llm:
            self.agent._llm.close()
        if self.tts:
            self.tts.stop()


def build_runtime(config_path: Path | None = None) -> AssistantRuntime:
    # Load environment variables from .env file
    load_dotenv()

    resolved_config_path = config_path or default_config_path()
    config = load_config(resolved_config_path)
    loggers = setup_logging(Path(config.logging.directory), config.logging.level)

    llm = LlamaCppClient(
        base_url=config.model.base_url,
        model=config.model.model,
        temperature=config.model.temperature,
        max_tokens=config.model.max_tokens,
        timeout_sec=config.model.request_timeout_sec,
        response_format=config.model.response_format,
    )
    tool_registry = ToolRegistry.load_builtin(config.tools)
    memory = LongTermMemory(Path(config.memory.db_path))
    conversation_store = ConversationStore(Path(config.memory.db_path))
    short_term = SessionMemory(conversation_store, max_tokens=800)
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
        ),
    )
    tts = _build_tts(config)
    stt = _build_stt(config)
    return AssistantRuntime(
        agent=agent,
        config=config,
        loggers=loggers,
        conversation_store=conversation_store,
        tts=tts,
        stt=stt,
    )