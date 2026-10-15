import threading
import time

from assistant.voice.controller import VoiceController


class FakeRecorder:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wav_bytes(self):
        return b"synthetic-wav"


def wait_for(controller, state):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if controller.snapshot()["state"] == state:
            return controller.snapshot()
        time.sleep(0.005)
    raise AssertionError(controller.snapshot())


def controller_for(
    *, transcribe=lambda _audio: "hello", submit=None, recorder_factory=FakeRecorder,
    wait_until_ready=None,
):
    submitted, published, interrupted = [], [], []

    def accept(text):
        submitted.append(text)
        return {"task_id": "task-1", "conversation_id": 7}

    controller = VoiceController(
        transcribe=transcribe,
        submit=submit or accept,
        interrupt=lambda: interrupted.append(True),
        publish=published.append,
        recorder_factory=recorder_factory,
        wait_until_ready=wait_until_ready,
    )
    return controller, submitted, published, interrupted


def test_press_release_submits_one_transcript_and_publishes_state():
    controller, submitted, published, interrupted = controller_for()
    try:
        start = controller.start()
        assert start["state"] == "listening"
        assert start["session_id"] == start["id"]
        assert interrupted == [True]
        controller.stop(start["session_id"])
        final = wait_for(controller, "submitted")
        assert submitted == ["hello"]
        assert final["task_id"] == "task-1"
        assert final["conversation_id"] == 7
        assert [event["state"] for event in published] == [
            "listening", "transcribing", "submitted"
        ]
        controller.stop(start["session_id"])
        assert submitted == ["hello"]
    finally:
        controller.close()


def test_cancel_while_transcribing_never_submits():
    entered, release = threading.Event(), threading.Event()

    def transcribe(_audio):
        entered.set()
        release.wait(2)
        return "old utterance"

    controller, submitted, _, _ = controller_for(transcribe=transcribe)
    try:
        session = controller.start()["session_id"]
        controller.stop(session)
        assert entered.wait(1)
        controller.cancel(session)
        release.set()
        controller.close()
        assert submitted == []
        assert controller.snapshot()["state"] == "cancelled"
    finally:
        release.set()
        controller.close()


def test_previous_transcription_cannot_change_new_recording():
    entered, release = threading.Event(), threading.Event()

    def transcribe(_audio):
        entered.set()
        release.wait(2)
        return "old utterance"

    controller, submitted, _, _ = controller_for(transcribe=transcribe)
    try:
        old = controller.start()["session_id"]
        controller.stop(old)
        assert entered.wait(1)
        current = controller.start()["session_id"]
        assert current != old
        controller.stop(old)
        controller.cancel(old)
        release.set()
        for worker in list(controller._workers):
            worker.join(1)
        assert controller.snapshot()["state"] == "listening"
        assert controller.snapshot()["session_id"] == current
        assert submitted == []
    finally:
        release.set()
        controller.close()


def test_microphone_start_failure_closes_recorder_and_sanitizes_error():
    class BrokenRecorder(FakeRecorder):
        def start(self):
            raise OSError("secret device details")  # noqa: TRY003 - synthetic failure

    recorder = BrokenRecorder()
    controller, submitted, published, _ = controller_for(recorder_factory=lambda: recorder)
    try:
        state = controller.start()
        assert state["state"] == "error"
        assert state["error"] == "Microphone unavailable. Check your audio input."
        assert recorder.stopped
        assert submitted == []
        assert "secret" not in str(published)
    finally:
        controller.close()


def test_transcription_error_is_not_a_prompt_or_sensitive_error():
    def fail(_audio):
        raise RuntimeError("provider key=secret")  # noqa: TRY003 - synthetic failure

    controller, submitted, published, _ = controller_for(transcribe=fail)
    try:
        controller.start()
        controller.stop()
        final = wait_for(controller, "error")
        assert final["error"] == "Could not transcribe that. Please try again."
        assert submitted == []
        assert "secret" not in str(published)
    finally:
        controller.close()


def test_silent_recording_has_useful_error():
    controller, submitted, _, _ = controller_for(transcribe=lambda _audio: "  ")
    try:
        controller.start()
        controller.stop()
        assert wait_for(controller, "error")["error"] == "I didn't catch that. Hold while speaking."
        assert submitted == []
    finally:
        controller.close()


def test_cancel_stops_recording_without_transcribing():
    recorder = FakeRecorder()
    controller, submitted, _, interrupted = controller_for(recorder_factory=lambda: recorder)
    try:
        session = controller.start()["session_id"]
        assert controller.cancel(session)["state"] == "cancelled"
        assert recorder.stopped
        assert submitted == []
        assert len(interrupted) == 2
    finally:
        controller.close()


def test_start_can_interrupt_wait_for_previous_task():
    waiting = threading.Event()

    def wait_until_ready(cancelled):
        waiting.set()
        cancelled.wait(2)
        return not cancelled.is_set()

    controller, submitted, _, _ = controller_for(wait_until_ready=wait_until_ready)
    try:
        old = controller.start()["session_id"]
        controller.stop(old)
        assert waiting.wait(1)
        new = controller.start()
        assert new["session_id"] != old
        assert new["state"] == "listening"
        assert submitted == []
    finally:
        controller.close()


def test_recording_stop_failure_cannot_submit_or_leave_listening_state():
    class BrokenRecorder(FakeRecorder):
        def stop(self):
            raise OSError("secret driver details")  # noqa: TRY003 - synthetic failure

    controller, submitted, published, _ = controller_for(recorder_factory=BrokenRecorder)
    try:
        controller.start()
        assert controller.stop()["state"] == "error"
        assert submitted == []
        assert "secret" not in str(published)
    finally:
        controller.close()


def test_submit_failure_publishes_actionable_error_without_exception_details():
    def submit(_text):
        raise RuntimeError("sensitive task context")  # noqa: TRY003 - synthetic failure

    controller, _, published, _ = controller_for(submit=submit)
    try:
        controller.start()
        controller.stop()
        final = wait_for(controller, "error")
        assert "could not start" in final["error"]
        assert "sensitive" not in str(published)
    finally:
        controller.close()
