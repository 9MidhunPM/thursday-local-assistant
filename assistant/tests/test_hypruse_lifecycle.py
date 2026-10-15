from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
import time
from types import SimpleNamespace

import pytest

from assistant.integrations.hypruse import HypruseBridge, HypruseConnectionError, HypruseTool
from assistant.tools.registry import ToolRegistry

_SERVER = """
import json, sys, time
mode = sys.argv[1]
calls = 0
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message["method"]
    if method == "initialize":
        if mode == "startup_hang":
            time.sleep(30)
        if mode == "startup_crash":
            sys.stderr.write("synthetic-private-startup-detail\\n")
            sys.exit(2)
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fixture", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "desktop", "description": "fixture",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    else:
        calls += 1
        if mode == "call_crash":
            time.sleep(0.15)
            sys.exit(3)
        if mode == "call_hang":
            time.sleep(30)
        result = {"content": [], "structuredContent": {"windows": [], "calls": calls}}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
    if mode == "idle_exit" and method == "tools/list":
        time.sleep(0.15)
        sys.exit(0)
"""


@pytest.fixture
def _prerequisites(monkeypatch):
    monkeypatch.setattr(HypruseBridge, "_dependencies", staticmethod(
        lambda: {"mcp": True, "hypruse": True}
    ))
    monkeypatch.setattr(HypruseBridge, "_session_present", staticmethod(lambda: True))


@pytest.fixture
def child_server(monkeypatch, _prerequisites):
    pytest.importorskip("mcp")
    real_spawn = asyncio.create_subprocess_exec
    processes, environments, modes = [], [], ["normal"]

    async def spawn(*_args, **kwargs):
        environments.append(kwargs["env"])
        mode = modes.pop(0) if len(modes) > 1 else modes[0]
        process = await real_spawn(sys.executable, "-c", _SERVER, mode, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return SimpleNamespace(processes=processes, environments=environments, modes=modes)


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("The fixture did not reach its expected state in time.")


def test_missing_dependencies_are_not_delivered_and_status_does_not_start(monkeypatch):
    monkeypatch.setattr(HypruseBridge, "_dependencies", staticmethod(
        lambda: {"mcp": False, "hypruse": False}
    ))
    bridge = HypruseBridge(ToolRegistry())
    assert bridge.status()["error_code"] == "missing_dependencies"
    assert bridge._thread is None
    result = HypruseTool(bridge).execute({}, SimpleNamespace())
    assert result["delivery"] == "not_delivered"
    assert result["error_code"] == "missing_dependencies"
    assert "not delivered" in result["verification"]
    assert bridge._thread is None
    assert not list(bridge.registry.tools())


def test_missing_session_is_distinguished_without_exposing_environment(monkeypatch):
    monkeypatch.setattr(HypruseBridge, "_dependencies", staticmethod(
        lambda: {"mcp": True, "hypruse": True}
    ))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "synthetic-private-session-value")
    bridge = HypruseBridge(ToolRegistry())
    with pytest.raises(HypruseConnectionError) as caught:
        bridge.call("desktop", {})
    assert caught.value.error_code == "missing_session"
    status = bridge.status()
    assert status["session_present"] is False
    assert "synthetic-private" not in json.dumps(status)
    assert bridge._thread is None


@pytest.mark.usefixtures("_prerequisites")
def test_exception_details_are_not_exposed(monkeypatch):
    bridge = HypruseBridge(ToolRegistry())

    async def fail():
        raise RuntimeError("synthetic-private-token-and-window-title")

    monkeypatch.setattr(bridge, "_serve", fail)
    with pytest.raises(HypruseConnectionError) as caught:
        bridge.connect()
    assert caught.value.error_code == "startup_failed"
    assert "synthetic-private" not in str(caught.value)
    assert "synthetic-private" not in json.dumps(bridge.status())
    bridge.close()


@pytest.mark.parametrize(("mode", "code"), [
    ("startup_hang", "startup_timeout"), ("startup_crash", "startup_failed"),
])
def test_startup_failure_is_bounded_and_cleans_child(child_server, mode, code):
    child_server.modes[:] = [mode]
    bridge = HypruseBridge(ToolRegistry(), startup_timeout=0.2)
    started = time.monotonic()
    try:
        with pytest.raises(HypruseConnectionError) as caught:
            bridge.call("desktop", {})
        assert caught.value.error_code == code
        assert caught.value.delivery == "not_delivered"
        assert time.monotonic() - started < 3
        assert child_server.processes[0].returncode is not None
        assert not list(bridge.registry.tools())
        assert "synthetic-private" not in json.dumps(bridge.status())
    finally:
        bridge.close()


def test_requests_share_one_lifetime_and_child_env_excludes_credentials(child_server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("THURSDAY_API_TOKEN", "synthetic-secret")
    bridge = HypruseBridge(ToolRegistry())
    try:
        assert bridge.call("desktop", {})["structuredContent"]["calls"] == 1
        worker = bridge._thread
        assert bridge.call("desktop", {})["structuredContent"]["calls"] == 2
        assert bridge._thread is worker
        assert len(child_server.processes) == 1
        assert bridge.status()["state"] == "ready"
        env = child_server.environments[0]
        assert "OPENAI_API_KEY" not in env
        assert "THURSDAY_API_TOKEN" not in env
        assert env["HYPRUSE_STRICT"] == "1"
        assert env["HYPRUSE_AUTH_GUARD"] == "strict"
        assert env["HYPRUSE_CLIPBOARD"] == "0"
    finally:
        bridge.close()
    assert child_server.processes[0].returncode is not None


def test_child_death_fails_inflight_and_queued_requests_then_observation_reconnects(child_server):
    child_server.modes[:] = ["call_crash", "normal"]
    bridge = HypruseBridge(ToolRegistry())
    bridge.connect()
    started = time.monotonic()
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            mutation = executor.submit(bridge.call, "keyboard", {"action": "key", "keys": "a"})
            wait_until(lambda: any(bridge._pending.values()))
            observation = executor.submit(bridge.call, "desktop", {})
            with pytest.raises(HypruseConnectionError) as caught:
                mutation.result(timeout=3)
            assert caught.value.delivery == "uncertain_delivery"
            with pytest.raises(HypruseConnectionError) as caught:
                observation.result(timeout=3)
            assert caught.value.delivery == "not_delivered"
        assert time.monotonic() - started < 3
        with pytest.raises(HypruseConnectionError) as caught:
            bridge.call("keyboard", {"action": "key", "keys": "a"})
        assert caught.value.error_code == "observation_required"
        assert len(child_server.processes) == 1
        assert bridge.call("desktop", {})["structuredContent"]["calls"] == 1
        assert len(child_server.processes) == 2
    finally:
        bridge.close()


def test_idle_child_exit_updates_status_without_another_request(child_server):
    child_server.modes[:] = ["idle_exit"]
    bridge = HypruseBridge(ToolRegistry())
    try:
        bridge.connect()
        wait_until(lambda: bridge.status()["state"] == "unavailable")
        assert bridge.status()["error_code"] == "connection_lost"
    finally:
        bridge.close()


def test_call_timeout_does_not_replay_mutation(child_server):
    child_server.modes[:] = ["call_hang", "normal"]
    bridge = HypruseBridge(ToolRegistry(), call_timeout=0.15)
    try:
        bridge.connect()
        with pytest.raises(HypruseConnectionError) as caught:
            bridge.call("keyboard", {"action": "key", "keys": "a"})
        assert caught.value.error_code == "call_timeout"
        assert caught.value.delivery == "uncertain_delivery"
        assert len(child_server.processes) == 1
        assert bridge.call("desktop", {})["structuredContent"]["calls"] == 1
        assert len(child_server.processes) == 2
    finally:
        bridge.close()


def test_stop_fails_inflight_request_and_reaps_child(child_server):
    child_server.modes[:] = ["call_hang"]
    bridge = HypruseBridge(ToolRegistry())
    bridge.connect()
    try:
        with concurrent.futures.ThreadPoolExecutor(1) as executor:
            request = executor.submit(bridge.call, "keyboard", {"action": "key", "keys": "a"})
            wait_until(lambda: any(bridge._pending.values()))
            bridge.close()
            with pytest.raises(HypruseConnectionError) as caught:
                request.result(timeout=1)
            assert caught.value.error_code == "closed"
            assert caught.value.delivery == "uncertain_delivery"
        assert child_server.processes[0].returncode is not None
        assert bridge.status()["state"] == "closed"
    finally:
        bridge.close()


def test_nonblocking_close_signals_before_new_observation_reconnects(child_server):
    child_server.modes[:] = ["call_hang", "normal"]
    bridge = HypruseBridge(ToolRegistry())
    bridge.connect()
    try:
        with concurrent.futures.ThreadPoolExecutor(1) as executor:
            request = executor.submit(bridge.call, "keyboard", {"action": "key", "keys": "a"})
            wait_until(lambda: any(bridge._pending.values()))
            old_worker = bridge._thread
            bridge.close(wait=False)
            assert bridge.status()["state"] == "closed"
            assert bridge._closing.is_set()
            with pytest.raises(HypruseConnectionError) as caught:
                request.result(timeout=1)
            assert caught.value.error_code == "closed"
            assert bridge.call("desktop", {})["structuredContent"]["calls"] == 1
            assert not old_worker.is_alive()
            assert bridge._thread is not old_worker
            assert bridge.status()["state"] == "ready"
            assert child_server.processes[0].returncode is not None
            assert bridge.call("desktop", {})["structuredContent"]["calls"] == 2
    finally:
        bridge.close()
