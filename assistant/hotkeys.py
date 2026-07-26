"""Global hotkey daemon for Thursday.

Works on Wayland (compositor-agnostic) by reading evdev key events directly.
Requires the user to be in the ``input`` group so /dev/input/event* is readable.

Hotkeys
-------
- **Super+Alt (hold)** — push-to-talk. Records microphone audio while held,
  shows a live transcript overlay (eww, falling back to dunst), and on release
  sends the final transcript to the Thursday web chat (/api/message) with TTS.

Run via ``run_hotkeys.sh`` (autostarted from hyprland.conf ``exec-once``).
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import selectors
import shutil
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar
from urllib import error, request

log = logging.getLogger("thursday.hotkeys")

# Linux input event codes, duplicated as constants so this module stays
# importable (and unit-testable) without evdev installed.
EV_KEY = 0x01
KEY_LEFTALT = 56
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126
KEY_C = 46

META_KEYS = frozenset({KEY_LEFTMETA, KEY_RIGHTMETA})
ALT_KEYS = frozenset({KEY_LEFTALT, KEY_RIGHTALT})

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK = 1024
MIN_AUDIO_BYTES = 8000  # ~0.25 s of 16 kHz s16 mono audio
INTERIM_INTERVAL_S = 1.6
INTERIM_MAX_SECONDS = 12  # only the tail is transcribed for live updates
FINAL_MAX_SECONDS = 55  # keep the final request under recognizer length limits
MAX_HOLD_SECONDS = 120  # safety: auto-stop if release events are ever lost
ANSWER_TIMEOUT_S = 180  # max wait for the agent's streamed response
DONE_VISIBLE_S = 6.0  # how long the finished answer stays on screen

# Integration files shared with the quickshell shell (ThursdayButton /
# ThursdayVoice). Paths are fixed by convention with ~/.config/quickshell.
VOICE_ACTIVE_FILE = Path("/tmp/thursday_voice_active")
VOICE_HUD_FILE = Path("/tmp/thursday_voice_overlay.json")


# ---------------------------------------------------------------------------
# Thursday server API
# ---------------------------------------------------------------------------


def base_url() -> str:
    host = os.getenv("THURSDAY_HOST", "127.0.0.1")
    port = os.getenv("THURSDAY_PORT", "5005")
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def _auth_headers() -> dict[str, str]:
    token = os.getenv("THURSDAY_API_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def server_healthy() -> bool:
    try:
        with request.urlopen(f"{base_url()}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def transcribe_wav(wav_bytes: bytes) -> str | None:
    """Send WAV audio to the Thursday server for transcription."""
    req = request.Request(
        f"{base_url()}/api/transcribe",
        data=wav_bytes,
        headers={"Content-Type": "audio/wav", **_auth_headers()},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read() or b"{}")
        text = (data.get("text") or "").strip()
        if text:
            return text
        if data.get("error"):
            log.warning("Transcribe error: %s", data["error"])
    except Exception as exc:
        log.warning("Transcribe request failed: %s", exc)
    return None


def send_message(text: str, *, tts: bool = True) -> bool:
    """Send a chat message to Thursday. Returns True if accepted."""
    req = request.Request(
        f"{base_url()}/api/message",
        data=json.dumps({"prompt": text, "tts": tts}).encode(),
        headers={"Content-Type": "application/json", **_auth_headers()},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except error.HTTPError as exc:
        log.warning("Message rejected (%s): %s", exc.code, exc.reason)
    except Exception as exc:
        log.warning("Message request failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Desktop integration
# ---------------------------------------------------------------------------


def parse_sse_event(line: str) -> tuple[str, dict] | None:
    """Parse one SSE line like 'data: {"type": ...}'. Returns (type, data)."""
    line = line.strip()
    if not line.startswith("data: "):
        return None
    try:
        event = json.loads(line[6:])
    except ValueError:
        return None
    if not isinstance(event, dict) or "type" not in event:
        return None
    data = event.get("data")
    return event["type"], data if isinstance(data, dict) else {}


def stream_answer(prompt: str, on_token, timeout: float = ANSWER_TIMEOUT_S) -> str | None:
    """Send *prompt* to Thursday and stream the response over SSE.

    on_token(partial_text) is called as chunks arrive. Returns the full
    answer, or None if the message was rejected / the stream failed.
    """
    sse_req = request.Request(
        f"{base_url()}/api/events?client=quickshell", headers=_auth_headers()
    )
    try:
        resp = request.urlopen(sse_req, timeout=10)
    except Exception as exc:
        log.warning("SSE connect failed: %s", exc)
        return None
    with resp:
        if not send_message(prompt, tts=True):
            return None
        chunks: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = resp.readline()
            except (ConnectionError, OSError) as exc:
                log.warning("SSE read failed: %s", exc)
                break
            if not raw:
                break
            parsed = parse_sse_event(raw.decode("utf-8", "replace"))
            if not parsed:
                continue  # keep-alive pings etc.
            etype, data = parsed
            if etype == "token":
                chunk = data.get("chunk") or ""
                if chunk:
                    chunks.append(chunk)
                    on_token("".join(chunks))
            elif etype == "final_response":
                content = (data.get("content") or "").strip()
                return content or ("".join(chunks).strip() or None)
            elif etype == "error":
                log.warning("Agent error: %s", data.get("content"))
                return "".join(chunks).strip() or None
        log.warning("Timed out waiting for the agent response")
        return "".join(chunks).strip() or None


def restore_a2dp() -> None:
    """Switch a Bluetooth card back to A2DP after recording (HSP) ends."""
    if not shutil.which("pactl"):
        return
    try:
        out = subprocess.run(
            ["pactl", "list", "cards", "short"], capture_output=True, text=True, timeout=3
        ).stdout
        card = next(
            (line.split()[0] for line in out.splitlines() if "bluez" in line.lower()), None
        )
        if not card:
            return
        for profile in ("a2dp-sink", "a2dp-sink-aac", "a2dp-sink-sbc", "a2dp-sink-sbc_xq"):
            done = subprocess.run(
                ["pactl", "set-card-profile", card, profile],
                capture_output=True,
                timeout=3,
            )
            if done.returncode == 0:
                return
    except Exception as exc:
        log.debug("a2dp restore failed: %s", exc)


class FallbackOverlay:
    """Visual fallback when quickshell is not running: eww, else dunst."""

    EWW_WINDOW = "thursday_voice"
    NOTIFY_ID = "2607"

    def __init__(self) -> None:
        self._eww = self._detect_eww()
        self._open = False

    @staticmethod
    def _detect_eww() -> bool:
        if not shutil.which("eww"):
            return False
        try:
            return subprocess.run(["eww", "ping"], capture_output=True, timeout=2).returncode == 0
        except Exception:
            return False

    def show(self, text: str, state: str = "listening") -> None:
        """Update overlay content. state: listening | busy | done | error."""
        if self._eww:
            try:
                subprocess.run(
                    [
                        "eww", "update",
                        f"thursday_voice_text={json.dumps(text)}",
                        f"thursday_voice_state={state}",
                    ],
                    capture_output=True,
                    timeout=3,
                )
                if not self._open:
                    subprocess.run(
                        ["eww", "open", self.EWW_WINDOW], capture_output=True, timeout=3
                    )
                    self._open = True
            except Exception as exc:
                log.debug("eww overlay failed, falling back to dunst: %s", exc)
                self._eww = False
            else:
                return
        timeout_ms = "0" if state in {"listening", "busy"} else "4000"
        subprocess.run(
            ["notify-send", "-r", self.NOTIFY_ID, "-t", timeout_ms, "Thursday", text],
            capture_output=True,
        )

    def hide(self) -> None:
        if self._eww and self._open:
            subprocess.run(["eww", "close", self.EWW_WINDOW], capture_output=True, timeout=3)
            self._open = False
        elif not self._eww:
            subprocess.run(
                ["notify-send", "-r", self.NOTIFY_ID, "-t", "1", "Thursday", " "],
                capture_output=True,
            )


def write_hud_file(state: str, transcript: str = "", answer: str = "") -> None:
    """Atomically publish voice HUD state for the quickshell overlay."""
    payload = json.dumps(
        {"state": state, "transcript": transcript, "answer": answer, "updated": time.time()}
    )
    try:
        tmp = VOICE_HUD_FILE.with_suffix(".tmp")
        tmp.write_text(payload)
        tmp.replace(VOICE_HUD_FILE)
    except OSError as exc:
        log.debug("HUD write failed: %s", exc)


def remove_hud_file() -> None:
    with contextlib.suppress(OSError):
        VOICE_HUD_FILE.unlink()


class VoiceHud:
    """Centered quickshell voice overlay (state file) + fallback visuals.

    States: listening | transcribing | answering | done | error.
    """

    # Map HUD states to the fallback overlay's simpler states.
    _FALLBACK_STATE: ClassVar[dict[str, str]] = {
        "transcribing": "busy",
        "thinking": "busy",
        "answering": "busy",
    }

    def __init__(self) -> None:
        self._fallback = FallbackOverlay()
        self._qs_running = False
        self._qs_checked = 0.0

    def _quickshell_running(self) -> bool:
        now = time.monotonic()
        if now - self._qs_checked > 2.0:
            self._qs_checked = now
            self._qs_running = (
                subprocess.run(["pgrep", "-x", "qs"], capture_output=True).returncode == 0
            )
        return self._qs_running

    def show(self, state: str, transcript: str = "", answer: str = "") -> None:
        write_hud_file(state, transcript, answer)
        if not self._quickshell_running():
            text = answer or (f"“{transcript}”" if transcript else state.capitalize() + "…")
            self._fallback.show(text, self._FALLBACK_STATE.get(state, state))

    def hide(self) -> None:
        remove_hud_file()
        self._fallback.hide()


# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------


class Recorder:
    """PyAudio push-to-talk recorder accumulating frames in memory."""

    def __init__(self) -> None:
        import pyaudio  # noqa: PLC0415  (optional dependency, imported lazily)

        self._pa = pyaudio.PyAudio()
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK,
            stream_callback=self._on_chunk,
            start=False,
        )

    def _on_chunk(self, in_data, _frame_count, _time_info, _status):
        with self._lock:
            self._frames.append(bytes(in_data))
        return None, 0  # paContinue

    def start(self) -> None:
        with self._lock:
            self._frames.clear()
        self._stream.start_stream()

    def stop(self) -> None:
        try:
            self._stream.stop_stream()
            self._stream.close()
        finally:
            self._pa.terminate()

    def wav_bytes(self, max_seconds: float | None = None) -> bytes:
        with self._lock:
            frames = list(self._frames)
        if max_seconds is not None:
            keep = int(max_seconds * SAMPLE_RATE / CHUNK)
            frames = frames[-keep:]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(b"".join(frames))
        return buf.getvalue()

    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)


def frames_to_seconds(frames: int) -> float:
    return frames * CHUNK / SAMPLE_RATE


def reuse_interim_as_final(
    last_interim: str, interim_frame_mark: int, total_frames: int
) -> str | None:
    """Return the interim text to use as the final transcript, or None.

    The last interim can stand in for a full re-transcription when it covered
    the *entire* recording and almost no new audio arrived after it — that
    makes release -> send feel instant instead of sitting on "Transcribing…".
    """
    if not last_interim or interim_frame_mark <= 0:
        return None
    interim_covered_everything = frames_to_seconds(total_frames) <= INTERIM_MAX_SECONDS + 1.0
    new_audio_seconds = frames_to_seconds(total_frames - interim_frame_mark)
    if interim_covered_everything and new_audio_seconds < 1.8:
        return last_interim
    return None


# ---------------------------------------------------------------------------
# Push-to-talk state machine
# ---------------------------------------------------------------------------


@dataclass
class ComboTracker:
    """Pure combo-hold detection over evdev key events (unit-testable)."""

    pressed: set[int] = field(default_factory=set)
    ptt_active: bool = False

    def handle(self, code: int, value: int) -> str | None:
        """Feed one key event. Returns 'ptt_start' / 'ptt_stop' / None.

        value: 1 = press, 0 = release, 2 = autorepeat (ignored).
        """
        if value == 2:
            return None
        if value == 1:
            self.pressed.add(code)
        elif value == 0:
            self.pressed.discard(code)
        else:
            return None

        combo = bool(self.pressed & META_KEYS) and bool(self.pressed & ALT_KEYS)
        if combo and not self.ptt_active:
            self.ptt_active = True
            return "ptt_start"
        if not combo and self.ptt_active:
            self.ptt_active = False
            return "ptt_stop"
        return None


class PushToTalk:
    """Orchestrates recording, live transcription and answer display.

    Each hold is a *session*: stale sessions stop touching the HUD so a new
    hold never fights the previous one (no flicker / breaks).
    """

    def __init__(self, hud: VoiceHud) -> None:
        self._hud = hud
        self._recorder: Recorder | None = None
        self._lock = threading.Lock()
        self._stop_interim = threading.Event()
        self._started_at = 0.0
        self._session = 0
        self._last_interim = ""
        self._interim_frame_mark = 0

    def start(self) -> None:
        with self._lock:
            if self._recorder is not None:
                return
            try:
                recorder = Recorder()
            except Exception as exc:
                log.warning("Microphone unavailable: %s", exc)
                self._hud.show("error", answer="Microphone unavailable")
                threading.Timer(1.5, self._hud.hide).start()
                return
            recorder.start()
            self._recorder = recorder
            self._started_at = time.monotonic()
            self._session += 1
            self._stop_interim.clear()
            self._last_interim = ""
        VOICE_ACTIVE_FILE.touch(exist_ok=True)
        log.info("PTT: listening…")
        self._hud.show("listening")
        threading.Thread(target=self._interim_loop, args=(self._session,), daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            recorder = self._recorder
            self._recorder = None
            # Bump the session so any in-flight interim transcription can't
            # overwrite the states that follow (transcribing/thinking/…).
            self._session += 1
            session = self._session
        if recorder is None:
            return
        self._stop_interim.set()
        recorder.stop()
        log.info("PTT: released, processing")
        threading.Thread(target=self._process, args=(recorder, session), daemon=True).start()

    def _interim_loop(self, session: int) -> None:
        while not self._stop_interim.wait(INTERIM_INTERVAL_S):
            recorder = self._recorder
            if recorder is None or session != self._session:
                return
            if time.monotonic() - self._started_at > MAX_HOLD_SECONDS:
                log.warning("PTT: max hold time reached, auto-stopping")
                self.stop()
                return
            mark = recorder.frame_count()
            wav = recorder.wav_bytes(max_seconds=INTERIM_MAX_SECONDS)
            if len(wav) < MIN_AUDIO_BYTES:
                continue
            text = transcribe_wav(wav)
            if text and session == self._session:
                self._last_interim = text
                self._interim_frame_mark = mark
                self._hud.show("listening", transcript=text)

    def _process(self, recorder: Recorder, session: int) -> None:
        def fresh() -> bool:
            return session == self._session

        try:
            total_frames = recorder.frame_count()
            if frames_to_seconds(total_frames) < 0.25:
                self._hud.show("error", answer="Too short — hold while speaking")
                time.sleep(1.5)
                return

            # Fast path: the last interim already covers (nearly) everything —
            # skip the slow full re-transcription entirely.
            text = reuse_interim_as_final(
                self._last_interim, self._interim_frame_mark, total_frames
            )
            if text is None:
                # Keep the last interim transcript on screen while the final
                # transcription runs — no visual gap between hold and answer.
                self._hud.show("transcribing", transcript=self._last_interim)
                wav = recorder.wav_bytes(max_seconds=FINAL_MAX_SECONDS)
                text = transcribe_wav(wav)
            if not text:
                self._hud.show("error", answer="Didn't catch that")
                time.sleep(1.5)
                return
            log.info("PTT transcript: %s", text)

            # Hearing is over: bar glyph back to idle, BT back to A2DP so the
            # spoken answer plays at full quality. Show the FULL transcript
            # right away — the overlay must not lag behind the backend.
            with contextlib.suppress(OSError):
                VOICE_ACTIVE_FILE.unlink()
            restore_a2dp()
            self._hud.show("thinking", transcript=text)

            last_write = [0.0]

            def on_token(partial: str) -> None:
                if not fresh():
                    return
                now = time.monotonic()
                if now - last_write[0] < 0.12:
                    return
                last_write[0] = now
                self._hud.show("answering", transcript=text, answer=partial)

            answer = stream_answer(text, on_token)
            if not fresh():
                return
            if answer is None:
                self._hud.show("error", transcript=text, answer="Thursday is busy — try again")
                time.sleep(3)
                return
            self._hud.show("done", transcript=text, answer=answer)
            time.sleep(DONE_VISIBLE_S)
        finally:
            with contextlib.suppress(OSError):
                VOICE_ACTIVE_FILE.unlink()
            if fresh():
                self._hud.hide()


# ---------------------------------------------------------------------------
# evdev main loop
# ---------------------------------------------------------------------------


def find_keyboards():
    """Yield evdev InputDevices that look like real keyboards."""
    import evdev  # noqa: PLC0415  (optional dependency, imported lazily)

    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        keys = dev.capabilities().get(EV_KEY, [])
        if KEY_LEFTMETA in keys and KEY_LEFTALT in keys and KEY_C in keys:
            yield dev


def run() -> None:
    import evdev  # noqa: PLC0415  (optional dependency, imported lazily)

    devices = list(find_keyboards())
    if not devices:
        log.error(
            "No readable keyboards found in /dev/input. "
            "Is your user in the 'input' group? (re-login after adding)"
        )
        sys.exit(1)
    for dev in devices:
        log.info("Watching keyboard: %s (%s)", dev.name, dev.path)

    selector = selectors.DefaultSelector()
    for dev in devices:
        selector.register(dev, selectors.EVENT_READ)

    tracker = ComboTracker()
    ptt = PushToTalk(VoiceHud())
    log.info("Hotkey daemon ready — hold Super+Alt to talk")

    while True:
        for key, _ in selector.select():
            dev = key.fileobj
            try:
                events = dev.read()
            except OSError:
                log.warning("Lost keyboard %s", dev.path)
                selector.unregister(dev)
                continue
            for event in events:
                if event.type != evdev.ecodes.EV_KEY:
                    continue
                action = tracker.handle(event.code, event.value)
                if action == "ptt_start":
                    ptt.start()
                elif action == "ptt_stop":
                    ptt.stop()


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("THURSDAY_HOTKEYS_LOG", "INFO").upper(), logging.INFO),
        format="[thursday-hotkeys] %(levelname)s %(message)s",
    )
    if not server_healthy():
        log.warning(
            "Thursday server not reachable at %s — start it with ./run.sh --web", base_url()
        )
    with contextlib.suppress(KeyboardInterrupt):
        run()


if __name__ == "__main__":
    main()
