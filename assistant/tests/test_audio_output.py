from __future__ import annotations

import json
import os
import threading
import time
from unittest.mock import Mock

import pytest

from assistant.voice import output


def obj(identifier, kind, props=None, **info):
    return {
        "id": identifier,
        "type": "PipeWire:Interface:" + kind,
        "info": {"props": props or {}, **info},
    }


def graph(stream_volume=0.6, sink_volume=0.8):
    return {
        1: obj(1, "Client", {"application.process.id": "100"}),
        2: obj(
            2,
            "Node",
            {"client.id": 1, "media.class": "Stream/Output/Audio"},
            state="running",
            params={
                "Props": [
                    {
                        "mute": False,
                        "channelVolumes": [stream_volume] * 2,
                        "softMute": False,
                        "softVolumes": [1, 1],
                        "volume": 1,
                    }
                ]
            },
        ),
        3: obj(
            3,
            "Node",
            {"media.class": "Audio/Sink"},
            params={"Props": [{"mute": False, "channelVolumes": [sink_volume] * 2}]},
        ),
        4: obj(4, "Link", state="active", **{"output-node-id": 2, "input-node-id": 3}),
    }


def test_client_pid_mapping_and_active_link_prove_output_ready():
    state = output.output_state(graph(), 100)
    assert state == {"known": True, "audible": True, "node_ids": [2], "reason": "ready"}
    assert set(state) == {"known", "audible", "node_ids", "reason"}


@pytest.mark.parametrize(("target", "field"), [(2, "mute"), (2, "softMute"), (3, "mute")])
def test_stream_or_sink_mute_prevents_audible(target, field):
    data = graph()
    data[target]["info"]["params"]["Props"][0][field] = True
    assert output.output_state(data, 100)["reason"] == "muted"
    assert not output.output_state(data, 100)["audible"]


@pytest.mark.parametrize(("stream", "sink"), [(0, 1), (1, 0)])
def test_zero_channel_volume_is_detected_despite_scalar_one(stream, sink):
    state = output.output_state(graph(stream, sink), 100)
    assert state["reason"] == "volume_zero"
    assert state["known"]
    assert not state["audible"]


def test_soft_zero_and_missing_controls_are_distinct():
    data = graph()
    props = data[2]["info"]["params"]["Props"][0]
    props["softVolumes"] = [0, 0]
    assert output.output_state(data, 100)["reason"] == "volume_zero"
    props.clear()
    props.update(mute=False, softVolumes=[1, 1])
    state = output.output_state(data, 100)
    assert state["reason"] == "unavailable"
    assert not state["known"]
    props["volume"] = 0.5
    assert output.output_state(data, 100)["audible"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "1", True])
def test_unknown_or_invalid_volume_cannot_prove_audibility(value):
    data = graph()
    data[2]["info"]["params"]["Props"][0]["channelVolumes"] = [value]
    assert not output.output_state(data, 100)["known"]


def test_inactive_link_is_not_an_output_route():
    data = graph()
    data[4]["info"]["state"] = "paused"
    assert output.output_state(data, 100)["reason"] == "unlinked"


def test_unrelated_process_does_not_count_as_owned_stream():
    state = output.output_state(graph(), 101)
    assert state == {"known": True, "audible": False, "node_ids": [], "reason": "no_stream"}


def test_partial_json_utf8_and_updates_preserve_prior_graph():
    parser = output._GraphParser()
    data = graph()
    data[1]["info"]["props"]["application.name"] = "Prøbe"
    payload = json.dumps(list(data.values()), ensure_ascii=False).encode()
    for byte in payload:
        parser.feed(bytes([byte]))
    assert output.output_state(parser.graph, 100)["audible"]
    parser.feed(b'[{"id":2,"info":{"params":{"Props":[{"channelVolumes":[0,0]}]}}}]')
    assert output.output_state(parser.graph, 100)["reason"] == "volume_zero"
    assert parser.graph[2]["info"]["params"]["Props"][0]["mute"] is False
    parser.feed(b'[{"id":4,"info":null}]')
    parser.feed(b'[{"id":2,"info":{"params":{"Props":[{"channelVolumes":[1,1]}]}}}]')
    assert output.output_state(parser.graph, 100)["reason"] == "unlinked"
    parser.feed(b'[{"id":1,"info":null}]')
    assert output.output_state(parser.graph, 100)["reason"] == "no_stream"


def test_bounded_incremental_parser(monkeypatch):
    monkeypatch.setattr(output, "MAX_BUFFER_BYTES", 40)
    with pytest.raises(ValueError, match="buffer"):
        output._GraphParser().feed(b"[" + b" " * 40)
    monkeypatch.setattr(output, "MAX_BUFFER_BYTES", 10000)
    monkeypatch.setattr(output, "MAX_OBJECTS", 1)
    parser = output._GraphParser()
    with pytest.raises(ValueError, match="object"):
        parser.feed(b'[{"id":1},{"id":2}]')
    parser = output._GraphParser()
    parser.feed(b'[{"id":1}]')
    with pytest.raises(ValueError, match="graph"):
        parser.feed(b'[{"id":2}]')


class Process:
    def __init__(self):
        read, self.writer = os.pipe()
        self.stdout = os.fdopen(read, "rb", buffering=0)
        self.returncode = None
        self.terminations = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminations += 1
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):  # noqa: ARG002 - Popen-compatible test double
        return self.returncode


def test_observer_periodic_publish_idempotence_and_no_stale_after_stop(monkeypatch):
    process = Process()
    spawn = Mock(return_value=process)
    monkeypatch.setattr(output.subprocess, "Popen", spawn)
    published = []
    repeated = threading.Event()

    def publish(state):
        published.append(state)
        if sum(item["audible"] for item in published) >= 2:
            repeated.set()

    observer = output.OutputObserver(100, publish)
    try:
        observer.start()
        observer.start()
        os.write(process.writer, json.dumps(list(graph().values())).encode())
        assert repeated.wait(1)
        observer.stop()
        observer.stop()
        count = len(published)
        time.sleep(0.25)
        assert len(published) == count
        spawn.assert_called_once()
        assert spawn.call_args.args[0] == ["pw-dump", "--monitor", "--no-colors"]
        assert process.terminations == 1
        assert process.stdout.closed
    finally:
        observer.stop()
        os.close(process.writer)


def test_missing_monitor_reports_sanitized_unavailable_periodically(monkeypatch):
    monkeypatch.setattr(output.subprocess, "Popen", Mock(side_effect=FileNotFoundError("private")))
    published = []
    repeated = threading.Event()

    def publish(state):
        published.append(state)
        if len(published) >= 2:
            repeated.set()

    observer = output.OutputObserver(100, publish)
    observer.start()
    try:
        assert repeated.wait(1)
        assert all(
            item == {"known": False, "audible": False, "node_ids": [], "reason": "unavailable"}
            for item in published
        )
    finally:
        observer.stop()


def test_idle_stream_scalar_zero_and_crossed_channels_are_not_audible():
    data = graph()
    data[2]["info"]["state"] = "idle"
    assert output.output_state(data, 100)["reason"] == "unlinked"
    data[2]["info"]["state"] = "running"
    props = data[2]["info"]["params"]["Props"][0]
    props["volume"] = 0
    assert output.output_state(data, 100)["reason"] == "volume_zero"
    props.update(volume=1, channelVolumes=[0, 1], softVolumes=[1, 0])
    assert output.output_state(data, 100)["reason"] == "volume_zero"


def test_parser_does_not_accumulate_unrelated_properties_or_large_channel_arrays():
    parser = output._GraphParser()
    for index in range(100):
        parser.feed(
            json.dumps(
                [
                    obj(
                        1,
                        "Client",
                        {
                            "application.process.id": 100,
                            f"private-{index}": "ignored" * 100,
                        },
                    )
                ]
            ).encode()
        )
    assert parser.graph[1]["info"]["props"] == {"application.process.id": 100}
    parser.feed(
        json.dumps(
            [
                obj(
                    2,
                    "Node",
                    params={
                        "Props": [
                            {
                                "mute": False,
                                "channelVolumes": [1] * 1000,
                            }
                        ]
                    },
                )
            ]
        ).encode()
    )
    assert parser.graph[2]["info"]["params"]["Props"][0]["channelVolumes"] is None
    assert len(json.dumps(parser.graph)) < 1000
