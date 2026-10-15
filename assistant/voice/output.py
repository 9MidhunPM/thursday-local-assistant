"""Observe only the requesting application's PipeWire output; never alter volume."""

from __future__ import annotations

# Fixed monitor failures are control flow and never include graph/private contents.
# ruff: noqa: TRY003, TRY004, TRY301
import codecs
import contextlib
import json
import math
import os
import selectors
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_BUFFER_BYTES = 4 * 1024 * 1024
MAX_OBJECTS = 4096
PUBLISH_INTERVAL = 0.2


def _id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if 0 <= result < 2**63 else None


def _info(node: dict) -> dict:
    value = node.get("info")
    return value if isinstance(value, dict) else {}


def _props(node: dict) -> dict:
    value = _info(node).get("props")
    return value if isinstance(value, dict) else {}


def _level(node: dict) -> str:
    params = _info(node).get("params", {})
    records = params.get("Props", []) if isinstance(params, dict) else []
    props: dict = {}
    for record in records if isinstance(records, list) else []:
        if isinstance(record, dict):
            props.update(record)
    # PipeWire represents an unused optional soft gain as an empty array.
    # Its absence does not invalidate known channel gain; null remains invalid.
    if props.get("softVolumes") == []:
        props.pop("softVolumes")
    if props.get("mute") is True or props.get("softMute") is True:
        return "muted"
    if any(key in props and not isinstance(props[key], bool) for key in ("mute", "softMute")):
        return "unavailable"
    # An absent optional control is different from an explicitly invalid update.
    # The parser preserves invalid controls as None so old valid gains cannot win.
    if "volume" in props:
        scalar = _number(props["volume"])
        if scalar is None or scalar < 0:
            return "unavailable"
    for key in ("channelVolumes", "softVolumes"):
        if key not in props:
            continue
        values = props[key]
        if not isinstance(values, list) or not values or len(values) > 64:
            return "unavailable"
        if any((number := _number(value)) is None or number < 0 for value in values):
            return "unavailable"
    levels = []
    for key in ("channelVolumes", "softVolumes"):
        value = props.get(key)
        if (
            isinstance(value, list)
            and value
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in value
            )
        ):
            levels.append(max(value))
    value = props.get("volume")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        levels.append(value)
    if levels and min(levels) <= 0:
        return "volume_zero"
    # A soft multiplier alone says nothing about the actual channel gain.
    main = props.get("channelVolumes")
    main_known = (
        isinstance(main, list)
        and bool(main)
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in main
        )
    )
    if "channelVolumes" not in props:
        value = props.get("volume")
        main_known = (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    soft = props.get("softVolumes")
    if main_known and isinstance(main, list) and isinstance(soft, list):
        if len(main) != len(soft) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in soft
        ):
            return "unavailable"
        if not any(hard * multiplier > 0 for hard, multiplier in zip(main, soft, strict=True)):
            return "volume_zero"
    mute_known = isinstance(props.get("mute"), bool)
    return "ready" if main_known and mute_known and levels else "unavailable"


def output_state(graph: dict[int, dict] | list[dict], parent_pid: int) -> dict:
    """Return only ownership IDs and output readiness, never graph/app metadata."""
    objects = list(graph.values()) if isinstance(graph, dict) else graph
    clients = {
        _id(item.get("id"))
        for item in objects
        if str(item.get("type", "")).endswith(":Client")
        and _id(item.get("id")) is not None
        and _id(_props(item).get("application.process.id")) == parent_pid
    }
    nodes = {
        _id(item.get("id")): item
        for item in objects
        if str(item.get("type", "")).endswith(":Node") and _id(item.get("id")) is not None
    }
    owned = sorted(
        identifier
        for identifier, item in nodes.items()
        if _props(item).get("media.class") == "Stream/Output/Audio"
        and (
            _id(_props(item).get("application.process.id")) == parent_pid
            or _id(_props(item).get("client.id")) in clients
        )
    )
    state = {"known": True, "audible": False, "node_ids": owned, "reason": "no_stream"}
    if not owned:
        return state
    edges: dict[int, set[int]] = {}
    for item in objects:
        info = _info(item)
        if not str(item.get("type", "")).endswith(":Link") or info.get("state") != "active":
            continue
        props = _props(item)
        source = _id(info.get("output-node-id", props.get("link.output.node")))
        target = _id(info.get("input-node-id", props.get("link.input.node")))
        if source is not None and target is not None:
            edges.setdefault(source, set()).add(target)
    reasons = []
    for identifier in owned:
        if _info(nodes[identifier]).get("state") != "running":
            reasons.append("unlinked")
            continue
        stream_level = _level(nodes[identifier])
        if stream_level in {"muted", "volume_zero"}:
            reasons.append(stream_level)
            continue
        sinks = []
        pending = list(edges.get(identifier, ()))
        visited = {identifier}
        while pending and len(visited) <= MAX_OBJECTS:
            target = pending.pop()
            if target in visited:
                continue
            visited.add(target)
            node = nodes.get(target, {})
            if _props(node).get("media.class") == "Audio/Sink":
                sinks.append(node)
            else:
                pending.extend(edges.get(target, ()))
        if not sinks:
            reasons.append("unlinked")
            continue
        sink_levels = [_level(sink) for sink in sinks]
        if stream_level == "ready" and "ready" in sink_levels:
            return {**state, "audible": True, "reason": "ready"}
        reasons.extend(sink_levels if stream_level == "ready" else [stream_level])
    reason = next(
        (
            value
            for value in ("unavailable", "muted", "volume_zero", "unlinked")
            if value in reasons
        ),
        "unavailable",
    )
    return {**state, "known": reason != "unavailable", "reason": reason}


def _merge(previous: dict, update: dict) -> dict:
    merged = dict(previous)
    for key, value in update.items():
        old = merged.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            merged[key] = _merge(old, value)
        elif key == "Props" and isinstance(old, list) and isinstance(value, list) and value:
            values = {}
            for item in old + value:
                if isinstance(item, dict):
                    values.update(item)
            merged[key] = [values]
        else:
            merged[key] = value
    return merged


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _filtered_update(item: dict) -> dict:
    """Bound retained state and discard unrelated application/private metadata."""
    result: dict = {"id": item["id"]}
    if "type" in item:
        result["type"] = str(item["type"])[:64]
    original = _info(item)
    info: dict = {}
    for key in ("input-node-id", "output-node-id"):
        if key in original:
            info[key] = _id(original[key])
    if "state" in original:
        info["state"] = str(original["state"])[:32]
    props = _props(item)
    selected = {}
    for key in ("application.process.id", "client.id", "link.output.node", "link.input.node"):
        if key in props:
            selected[key] = _id(props[key])
    if "media.class" in props:
        selected["media.class"] = str(props["media.class"])[:64]
    if selected:
        info["props"] = selected
    params = original.get("params")
    if isinstance(params, dict) and "Props" in params:
        selected = {}
        records = params["Props"]
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            for key in ("mute", "softMute"):
                if key in record:
                    selected[key] = record[key] if isinstance(record[key], bool) else None
            if "volume" in record:
                selected["volume"] = _number(record["volume"])
            for key in ("channelVolumes", "softVolumes"):
                if key in record:
                    values = record[key]
                    selected[key] = (
                        [_number(value) for value in values]
                        if isinstance(values, list) and len(values) <= 64
                        else None
                    )
        info["params"] = {"Props": [selected] if selected else []}
    if info:
        result["info"] = info
    return result


class _GraphParser:
    def __init__(self) -> None:
        self.graph: dict[int, dict] = {}
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.json_decoder = json.JSONDecoder()
        self.observed = False

    def feed(self, data: bytes) -> None:
        self.buffer += self.decoder.decode(data)
        if len(self.buffer.encode("utf-8")) > MAX_BUFFER_BYTES:
            raise ValueError("PipeWire monitor buffer limit.")
        while self.buffer.strip():
            self.buffer = self.buffer.lstrip()
            try:
                items, end = self.json_decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                return
            if not isinstance(items, list) or len(items) > MAX_OBJECTS:
                raise ValueError("PipeWire monitor object limit.")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid PipeWire monitor object.")
                identifier = _id(item.get("id"))
                if identifier is None:
                    raise ValueError("Invalid PipeWire monitor identifier.")
                if "info" in item and item["info"] is None:
                    self.graph.pop(identifier, None)
                else:
                    self.graph[identifier] = _merge(
                        self.graph.get(identifier, {}), _filtered_update(item)
                    )
                if len(self.graph) > MAX_OBJECTS:
                    raise ValueError("PipeWire monitor graph limit.")
            self.observed = True
            self.buffer = self.buffer[end:]


def _terminate(process) -> None:
    if process is None:
        return
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
    except (OSError, subprocess.SubprocessError):
        pass
    finally:
        if process.stdout is not None:
            process.stdout.close()


class OutputObserver:
    def __init__(self, parent_pid: int, publish: Callable[[dict], None]) -> None:
        self.parent_pid = parent_pid
        self.publish = publish
        self._lock = threading.RLock()
        self._generation = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._generation += 1
            generation = self._generation
            stopped = threading.Event()
            self._stop = stopped
            self._thread = threading.Thread(
                target=self._run,
                args=(generation, stopped),
                daemon=True,
                name="thursday-output-observer",
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop.set()
            thread, process = self._thread, self._process
            self._thread, self._process = None, None
        _terminate(process)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def _emit(self, generation: int, stopped: threading.Event, state: dict) -> None:
        with self._lock:
            if generation == self._generation and not stopped.is_set():
                with contextlib.suppress(Exception):
                    self.publish(state)

    def _run(self, generation: int, stopped: threading.Event) -> None:
        process = None
        selector = selectors.DefaultSelector()
        parser = _GraphParser()
        unavailable = {"known": False, "audible": False, "node_ids": [], "reason": "unavailable"}
        state = unavailable
        try:
            try:
                process = subprocess.Popen(
                    ["pw-dump", "--monitor", "--no-colors"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                with self._lock:
                    if generation != self._generation or stopped.is_set():
                        return
                    self._process = process
                selector.register(process.stdout, selectors.EVENT_READ)
            except (OSError, ValueError):
                _terminate(process)
                process = None
            last_publish = 0.0
            while not stopped.is_set():
                if process is not None:
                    try:
                        if process.poll() is not None:
                            raise OSError("PipeWire monitor exited.")
                        for key, _mask in selector.select(timeout=0.05):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                raise OSError("PipeWire monitor closed.")
                            parser.feed(chunk)
                            if parser.observed:
                                state = output_state(parser.graph, self.parent_pid)
                    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                        selector.close()
                        _terminate(process)
                        process = None
                        state = unavailable
                else:
                    stopped.wait(0.05)
                now = time.monotonic()
                if now - last_publish >= PUBLISH_INTERVAL:
                    self._emit(generation, stopped, state)
                    last_publish = now
        finally:
            selector.close()
            _terminate(process)
            with self._lock:
                if self._generation == generation:
                    self._process = None
