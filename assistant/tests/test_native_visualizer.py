"""Run the same measured-level calculation used by the native QML waveform."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def readings(cases):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise native QML JavaScript")
    source = (ROOT / "desktop/quickshell/SpeechLevels.js").read_text()
    source = source.replace(".pragma library", "")
    script = (source + "\nconsole.log(JSON.stringify(" + json.dumps(cases)
              + ".map(args => reading(...args))));")
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_waveform_uses_only_measured_past_and_current_audio():
    envelope = {"interval_ms": 40, "levels": [0.1, 0.7, 0.4, 1.0]}
    first, next_frame = readings([[envelope, 0, True, 3], [envelope, 80, True, 3]])
    assert first == {"level": 0.1, "bars": [0, 0, 0.1], "available": True}
    assert next_frame == {"level": 0.4, "bars": [0.1, 0.7, 0.4], "available": True}


def test_silence_clears_history_and_stopped_loading_or_missing_audio_stays_flat():
    envelope = {"interval_ms": 40, "levels": [0.6, 0.9, 0, 0.8]}
    cases = [[envelope, 80, True, 3], [envelope, 40, False, 3], [None, 40, True, 3],
             [envelope, 160, True, 3], [envelope, -1, True, 3]]
    for result in readings(cases):
        assert result["level"] == 0
        assert result["bars"] == [0, 0, 0]


def test_malformed_levels_do_not_invent_motion_or_escape_bounds():
    malformed = {"interval_ms": 40, "levels": [-1, "0.8", None, 8]}
    results = readings([[malformed, position, True, 3] for position in [0, 40, 80, 120]])
    assert [result["level"] for result in results] == [0, 0, 0, 1]
    assert results[-1]["bars"] == [0, 0, 1]


def test_native_playing_feedback_waits_for_qt_and_waveform_has_no_synthetic_loop():
    source = (ROOT / "desktop/quickshell/Thursday.qml").read_text()
    next_audio = source.split("function nextAudio()", 1)[1].split("function reportPlaying()", 1)[0]
    assert 'feedback("playing"' not in next_audio
    assert "player.hasAudio" in source
    assert "player.playbackState === MediaPlayer.PlayingState" in source
    assert "root.playbackPosition = player.position" in source
    assert "device: devices.defaultAudioOutput" in source
    assert "!root.reducedMotion && root.recording" in source
    diagnostic = source.split("function audioStatus(): string", 1)[1].split("\n    Process {", 1)[0]
    assert "selected_device: root.audioDeviceInfo(output.device)" in diagnostic
    assert "available_devices: available" in diagnostic
    assert "error_code: player.error" in diagnostic
    assert "errorString" not in diagnostic
    assert "player.source" not in diagnostic
    meter = (ROOT / "desktop/quickshell/SpeechVisualizer.qml").read_text()
    assert "Animation" not in meter
    assert "Math.random" not in meter


def test_voice_status_is_targeted_recovers_and_keeps_existing_audio_playing():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise native QML JavaScript")
    source = (ROOT / "desktop/quickshell/Thursday.qml").read_text()
    status_case = source.split('case "tts_status":', 1)[1].split('case "tts_stop":', 1)[0]
    handle = ('with (state) { switch ("tts_status") { case "tts_status":'
              + status_case + "} } return state;")
    base = {"generation": "active", "retiredGenerations": ["old"], "speechPreparing": True,
            "speechError": "Previous synthesis error", "speechNotice": "Previous fallback",
            "voiceError": "microphone notice",
            "hudDismissed": True, "playingAudio": {"audio_id": "existing"}}
    events = [
        {"client_id": "native", "generation_id": "active", "state": "unavailable",
         "message": "No voice"},
        {"client_id": "native", "generation_id": "active", "state": "fallback",
         "message": "Local voice"},
        {"client_id": "native", "generation_id": "active", "state": "ready"},
        {"client_id": "web", "generation_id": "active", "state": "unavailable"},
        {"client_id": "native", "generation_id": "old", "state": "unavailable"},
    ]
    script = ("const handle = new Function('state', 'data', " + json.dumps(handle) + ");"
              + "const base = " + json.dumps(base) + ";"
              + "const events = " + json.dumps(events) + ";"
              + "console.log(JSON.stringify(events.map(event => "
              + "handle(structuredClone(base), event))));")
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    unavailable, fallback, ready, other_client, retired = json.loads(result.stdout)
    assert unavailable["speechError"] == "No voice"
    assert not unavailable["speechNotice"]
    assert not unavailable["speechPreparing"]
    assert not unavailable["hudDismissed"]
    assert fallback["speechNotice"] == "Local voice"
    assert not fallback["speechError"]
    assert not ready["speechNotice"]
    assert not ready["speechError"]
    assert other_client == base
    assert retired == base
    for state in [unavailable, fallback, ready]:
        assert state["playingAudio"] == base["playingAudio"]
        assert state["voiceError"] == base["voiceError"]


def test_system_output_gate_clears_false_speech_and_recovers_without_blocking_play():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed to exercise native QML JavaScript")
    source = (ROOT / "desktop/quickshell/Thursday.qml").read_text()
    expression = source.split("readonly property bool speaking:", 1)[1].split("\n", 1)[0]
    notice = source.split("readonly property string outputNotice: {", 1)[1]
    notice = notice.split("\n    }", 1)[0]
    events = [{"known": True, "audible": False, "reason": reason}
              for reason in ["volume_zero", "muted", "unlinked"]]
    events += [{"known": False, "audible": False, "reason": "checking"},
               {"known": True, "audible": True, "reason": "ready"},
               {"known": False, "audible": True, "reason": "checking"}]
    script = (
        "const speaking = new Function('playbackRunning', 'nativeOutput', "
        + json.dumps("return " + expression) + ");"
        + "const notice = new Function('playingAudio', 'playbackRunning', 'speaking', "
        + "'outputSetupComplete', "
        + "'nativeOutput', " + json.dumps(notice) + ");"
        + "const events = " + json.dumps(events) + ";"
        + "console.log(JSON.stringify(events.map(output => { "
        + "const active = speaking(true, output); return {speaking: active, "
        + "notice: notice({}, true, active, true, output), "
        + "setup_notice: notice({}, true, active, false, output)}; })));"
    )
    result = subprocess.run([node, "-e", script], text=True, capture_output=True, check=True)
    states = json.loads(result.stdout)
    for blocked in states[:3]:
        assert not blocked["speaking"]
        assert blocked["notice"]
    for state in states:
        assert state["setup_notice"] == ""
    assert states[3] == {"speaking": False, "notice": "", "setup_notice": ""}
    assert states[4] == {"speaking": True, "notice": "", "setup_notice": ""}
    assert states[5] == {"speaking": False, "notice": "", "setup_notice": ""}
    next_audio = source.split("function nextAudio()", 1)[1].split("function reportPlaying()", 1)[0]
    assert "player.play()" in next_audio
    assert "if (nativeOutput" not in next_audio
    assert "if (!nativeOutput" not in next_audio
