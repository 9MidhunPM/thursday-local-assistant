from __future__ import annotations

import threading

import pytest

from assistant.integrations.browser_bridge import BrowserBridge, BrowserBridgeError


def test_browser_bridge_round_trip() -> None:
    bridge = BrowserBridge(token="test")
    result: dict[str, object] = {}

    def requester() -> None:
        result.update(bridge.request("gmail.read_inbox", {"max_messages": 20}, timeout=1))

    thread = threading.Thread(target=requester)
    thread.start()
    command = bridge.next_command(timeout=1)
    assert command is not None
    assert bridge.status()["connected"] is True
    assert bridge.status()["pending"] == 1
    assert command["action"] == "gmail.read_inbox"
    assert command["payload"] == {"max_messages": 20}
    assert bridge.resolve(command["id"], success=True, data={"messages": []})
    thread.join(timeout=1)

    assert result == {"messages": []}
    assert bridge.status()["pending"] == 0
    assert bridge.wait_until_connected(timeout=0.01) is True


def test_browser_bridge_times_out_when_extension_is_not_connected() -> None:
    bridge = BrowserBridge(token="test")

    with pytest.raises(BrowserBridgeError, match="Open Brave normally"):
        bridge.request("gmail.read_inbox", timeout=0.01)


def test_browser_bridge_surfaces_extension_error() -> None:
    bridge = BrowserBridge(token="test")
    error: list[str] = []

    def requester() -> None:
        try:
            bridge.request("gmail.read_inbox", timeout=1)
        except BrowserBridgeError as exc:
            error.append(str(exc))

    thread = threading.Thread(target=requester)
    thread.start()
    command = bridge.next_command(timeout=1)
    assert command is not None
    assert bridge.resolve(command["id"], success=False, error="Gmail did not load")
    thread.join(timeout=1)

    assert error == ["Gmail did not load"]


def test_browser_bridge_requires_the_install_token() -> None:
    bridge = BrowserBridge(token="correct-token")
    assert bridge.authenticate("correct-token")
    assert not bridge.authenticate("wrong-token")
    assert not bridge.authenticate(None)


def test_browser_bridge_rejects_unknown_actions() -> None:
    bridge = BrowserBridge(token="test")
    with pytest.raises(BrowserBridgeError, match="Unsupported"):
        bridge.request("browser.eval", {"script": "alert(1)"}, timeout=0.01)
