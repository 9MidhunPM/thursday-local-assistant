from __future__ import annotations

from abc import ABC, abstractmethod


class SpeechToText(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str) -> str:
        raise NotImplementedError


class TextToSpeech(ABC):
    @abstractmethod
    def synthesize(self, text: str, output_path: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass
