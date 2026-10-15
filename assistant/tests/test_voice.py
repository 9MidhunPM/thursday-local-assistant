from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest

from assistant.config.loader import load_config
from assistant.runtime import _build_tts, default_config_path
from assistant.voice import EdgeTTS, SpeechRecognitionSTT
from assistant.voice.base import SpeechToText, TextToSpeech


def test_base_classes_abstract():
    with pytest.raises(TypeError):
        SpeechToText()  # type: ignore
    with pytest.raises(TypeError):
        TextToSpeech()  # type: ignore


def test_edge_tts_importable():
    assert issubclass(EdgeTTS, TextToSpeech)


@patch("assistant.voice.tts.metadata.version", return_value="7.3.1")
@patch("assistant.voice.tts.subprocess.run")
def test_edge_tts_startup_probe_is_local_version_check(run, version_metadata):
    run.return_value = subprocess.CompletedProcess([], 0, stdout="edge-tts 7.3.1", stderr="")
    binary = EdgeTTS._find_edge_tts()
    assert binary.endswith("edge-tts")
    assert run.call_args.args[0][-1] == "--version"
    version_metadata.assert_called_once_with("edge-tts")


@pytest.mark.parametrize("version", ["7.2.8", "7.3.0", "8.0.0", "7.3.1rc0"])
def test_edge_tts_rejects_incompatible_handshake_version(monkeypatch, version):
    monkeypatch.setattr("assistant.voice.tts.metadata.version", lambda _name: version)
    with pytest.raises(FileNotFoundError, match=r"edge-tts>=7\.3\.1,<8"):
        EdgeTTS(engine="online")


def test_neural_engine_can_run_without_installed_online_package(monkeypatch):
    def missing(_name):
        message = "Online package metadata must not be inspected for local speech"
        raise AssertionError(message)

    monkeypatch.setattr("assistant.voice.tts.metadata.version", missing)
    assert EdgeTTS(engine="neural")._engine == "neural"


def test_speech_recognition_stt_importable():
    assert issubclass(SpeechRecognitionSTT, SpeechToText)


def test_config_integration(monkeypatch):
    # Public defaults must not depend on private settings loaded by runtime tests.
    for name in (
        "THURSDAY_TTS_ENGINE", "THURSDAY_TTS_VOICE", "THURSDAY_TTS_RATE",
        "THURSDAY_TTS_LOCAL_VOICE", "THURSDAY_TTS_SPEED", "THURSDAY_TTS_EMERGENCY_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    config = load_config(default_config_path())
    assert config.voice.tts_enabled
    assert config.voice.tts_voice == "en-GB-RyanNeural"
    assert config.voice.tts_rate == "+0%"
    assert config.voice.tts_engine == "online"
    assert config.voice.tts_local_voice == "bm_george"
    assert config.voice.tts_speed == 1.0
    assert not config.voice.tts_emergency_fallback
    assert config.voice.stt_enabled
    assert config.voice.stt_recognizer == "google"
    assert config.voice.stt_language == "en-US"


def test_neural_voice_settings_reach_runtime(monkeypatch):
    monkeypatch.setenv("THURSDAY_TTS_ENGINE", "neural")
    monkeypatch.setenv("THURSDAY_TTS_LOCAL_VOICE", "bm_lewis")
    monkeypatch.setenv("THURSDAY_TTS_SPEED", "0.95")
    monkeypatch.setenv("THURSDAY_TTS_EMERGENCY_FALLBACK", "false")
    tts = _build_tts(load_config(default_config_path()))
    assert tts._engine == "neural"
    assert tts._local_voice == "bm_lewis"
    assert tts._speed == 0.95
    assert not tts._emergency_fallback


def test_provider_failure_is_never_transcript():
    stt = SpeechRecognitionSTT(recognizer="google")
    recognizer = Mock()
    recognizer.recognize_google.side_effect = RuntimeError("provider-token-secret")
    with pytest.raises(RuntimeError, match="Could not transcribe audio") as raised:
        stt._recognize(recognizer, object())
    assert "secret" not in str(raised.value)


def test_auto_falls_back_after_provider_error():
    stt = SpeechRecognitionSTT(recognizer="auto")
    recognizer = Mock()
    recognizer.recognize_google.side_effect = RuntimeError("network unavailable")
    recognizer.recognize_whisper.return_value = "recognized words"
    assert stt._recognize(recognizer, object()) == "recognized words"
    recognizer.recognize_sphinx.assert_not_called()


def test_google_uses_configured_language():
    stt = SpeechRecognitionSTT(language="en-IN")
    recognizer = Mock()
    recognizer.recognize_google.return_value = "hello"
    audio = object()
    assert stt._recognize(recognizer, audio) == "hello"
    recognizer.recognize_google.assert_called_once_with(audio, language="en-IN")
