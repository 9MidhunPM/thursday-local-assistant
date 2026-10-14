from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from assistant.voice.base import SpeechToText


class SpeechRecognitionSTT(SpeechToText):
    def __init__(
        self,
        energy_threshold: int = 300,
        pause_threshold: float = 0.8,
        timeout: float = 5.0,
        phrase_time_limit: float | None = None,
        language: str = "en-US",
        recognizer: str = "google",
    ) -> None:
        self._energy_threshold = energy_threshold
        self._pause_threshold = pause_threshold
        self._timeout = timeout
        self._phrase_time_limit = phrase_time_limit
        self._language = language
        self._recognizer_backend = recognizer
        self._sr = self._import_sr()

    @staticmethod
    def _import_sr():
        try:
            import speech_recognition as sr  # noqa: PLC0415 - optional voice dependency
        except ImportError as exc:
            message = "SpeechRecognition is required. Install via: pip install SpeechRecognition"
            raise ImportError(message) from exc
        return sr

    def transcribe(self, audio_path: str) -> str:
        r = self._sr.Recognizer()
        with self._sr.AudioFile(audio_path) as source:
            audio = r.record(source)
        return self._recognize(r, audio)

    def transcribe_bytes(
        self, audio_data: bytes, sample_width: int = 2, sample_rate: int = 16000
    ) -> str:
        r = self._sr.Recognizer()
        audio = self._sr.AudioData(audio_data, sample_rate, sample_width)
        return self._recognize(r, audio)

    def listen(self) -> str:
        r = self._sr.Recognizer()
        r.energy_threshold = self._energy_threshold
        r.pause_threshold = self._pause_threshold

        mic = self._get_microphone()
        with mic as source:
            r.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = r.listen(
                    source,
                    timeout=self._timeout,
                    phrase_time_limit=self._phrase_time_limit,
                )
            except self._sr.WaitTimeoutError:
                return ""

        return self._recognize(r, audio)

    def _get_microphone(self) -> Any:
        try:
            return self._sr.Microphone()
        except OSError as e:
            message = "No microphone found. Make sure your microphone is connected and accessible."
            raise RuntimeError(message) from e

    def _recognize(self, recognizer: Any, audio: Any) -> str:
        backends = []

        if self._recognizer_backend in ("google", "auto"):
            backends.append(self._try_google)
        if self._recognizer_backend in ("whisper", "auto"):
            backends.append(self._try_whisper)
        if self._recognizer_backend in ("sphinx", "auto"):
            backends.append(self._try_sphinx)

        for backend in backends:
            result = backend(recognizer, audio)
            if result is not None:
                return result

        # Provider exceptions are failures, never recognized speech. Returning
        # an exception's text here previously sent it to the agent as a prompt.
        message = "Could not transcribe audio. Please try again."
        raise RuntimeError(message)

    def _try_google(self, recognizer: Any, audio: Any) -> str | None:
        try:
            return recognizer.recognize_google(audio, language=self._language)
        except Exception:
            return None

    @staticmethod
    def _try_whisper(recognizer: Any, audio: Any) -> str | None:
        try:
            return recognizer.recognize_whisper(audio)
        except Exception:
            return None

    @staticmethod
    def _try_sphinx(recognizer: Any, audio: Any) -> str | None:
        try:
            return recognizer.recognize_sphinx(audio)
        except Exception:
            return None

    @staticmethod
    def record_to_file(
        output_path: str,
        duration: int = 5,
        sample_rate: int = 16000,
    ) -> str:
        try:
            import speech_recognition as sr  # noqa: PLC0415 - optional voice dependency
        except ImportError as exc:
            message = "SpeechRecognition is required."
            raise ImportError(message) from exc

        r = sr.Recognizer()
        with sr.Microphone(sample_rate=sample_rate) as source:
            r.adjust_for_ambient_noise(source, duration=0.5)
            audio = r.record(source, duration=duration)

        with NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            wav_data = audio.get_wav_data()
            f.write(wav_data)

        final_path = output_path or temp_path
        if output_path:
            with open(output_path, "wb") as f:
                f.write(audio.get_wav_data())
            if temp_path and temp_path != output_path:
                Path(temp_path).unlink()

        return final_path
