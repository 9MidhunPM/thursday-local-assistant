from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from assistant.config.loader import load_config  # noqa: E402
from assistant.runtime import default_config_path  # noqa: E402
from assistant.voice import EdgeTTS, SpeechRecognitionSTT  # noqa: E402
from assistant.voice.base import SpeechToText, TextToSpeech  # noqa: E402


class VoiceInterfaceTests(TestCase):
    def test_base_classes_abstract(self) -> None:
        with self.assertRaises(TypeError):
            SpeechToText()  # type: ignore
        with self.assertRaises(TypeError):
            TextToSpeech()  # type: ignore

    def test_edge_tts_importable(self) -> None:
        self.assertTrue(issubclass(EdgeTTS, TextToSpeech))

    def test_speech_recognition_stt_importable(self) -> None:
        self.assertTrue(issubclass(SpeechRecognitionSTT, SpeechToText))

    def test_config_integration(self) -> None:
        config = load_config(default_config_path())
        self.assertTrue(config.voice.tts_enabled)
        self.assertEqual(config.voice.tts_voice, "en-US-EmmaMultilingualNeural")
        self.assertEqual(config.voice.tts_rate, "+25%")
        self.assertTrue(config.voice.stt_enabled)
        self.assertEqual(config.voice.stt_recognizer, "google")
        self.assertEqual(config.voice.stt_language, "en-US")
