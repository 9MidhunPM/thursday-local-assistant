from __future__ import annotations

import json
import subprocess
import threading
from types import SimpleNamespace

import pytest

from assistant import web_launch


@pytest.fixture(autouse=True)
def private_launch_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        web_launch, "runtime_dir", lambda _name: web_launch.private_dir(tmp_path / "runtime")
    )
    monkeypatch.setattr(web_launch, "_has_hyprland", lambda: False)


def test_web_launcher_starts_supervised_backend_and_opens_dedicated_app(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(web_launch, "_wait_for_web", lambda url: commands.append(("ready", url)))
    monkeypatch.setattr(web_launch, "_focus_existing", lambda _url: False)
    monkeypatch.setattr(
        web_launch.shutil, "which", lambda name: "/usr/bin/brave" if name == "brave" else None
    )
    monkeypatch.setattr(
        web_launch.subprocess, "run", lambda command, **_kwargs: commands.append(command)
    )
    monkeypatch.setattr(
        web_launch.subprocess, "Popen", lambda command, **_kwargs: commands.append(command)
    )

    result = web_launch.open_web_app("http://127.0.0.1:5015", start_service=True)

    assert commands[0] == ["systemctl", "--user", "start", "thursday.service"]
    assert commands[1] == ("ready", "http://127.0.0.1:5015")
    assert "--app=http://127.0.0.1:5015/desktop/5015" in commands[2]
    assert any(argument.startswith("--class=thursday-web-") for argument in commands[2])
    assert result == {"status": "launch_requested", "reused": False}


def test_web_launcher_focuses_existing_app_without_another_window(monkeypatch):
    monkeypatch.setattr(web_launch, "_wait_for_web", lambda _url: None)
    monkeypatch.setattr(web_launch, "_focus_existing", lambda _url: True)
    monkeypatch.setattr(
        web_launch.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("duplicate app")
    )
    assert web_launch.open_web_app("http://127.0.0.1:5005") == {
        "status": "launch_requested",
        "reused": True,
    }


def test_url_derived_chromium_app_class_reuses_the_matching_local_app(monkeypatch):
    calls = []
    monkeypatch.setattr(web_launch.shutil, "which", lambda _name: "/usr/bin/hyprctl")
    monkeypatch.setattr(
        web_launch, "_focus_window", lambda address: calls.append(("focus", address))
    )

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            stdout=json.dumps(
                [
                    {
                        "class": "brave-127.0.0.1__desktop_5005-Default",
                        "title": "Thursday - AI Assistant",
                        "address": "0x1234",
                        "pid": 123,
                    }
                ]
            )
        )

    monkeypatch.setattr(web_launch.subprocess, "run", run)
    assert web_launch._focus_existing("http://127.0.0.1:5005")
    assert calls[-1] == ("focus", "0x1234")
    calls.clear()
    assert not web_launch._focus_existing("http://127.0.0.1:5015")
    assert len(calls) == 1


def test_known_web_window_focus_failure_does_not_launch_a_duplicate(monkeypatch):
    monkeypatch.setattr(web_launch, "_wait_for_web", lambda _url: None)
    monkeypatch.setattr(web_launch.shutil, "which", lambda _name: "/usr/bin/hyprctl")
    monkeypatch.setattr(
        web_launch.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps(
                [
                    {
                        "class": "brave-127.0.0.1__desktop_5005-Default",
                        "title": "Thursday - AI Assistant",
                        "address": "0x1234",
                    }
                ]
            )
        ),
    )

    def fail_focus(_address):
        message = "The existing web window could not be focused."
        raise web_launch.WebLaunchError(message)

    monkeypatch.setattr(web_launch, "_focus_window", fail_focus)
    monkeypatch.setattr(
        web_launch.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("duplicate app")
    )
    with pytest.raises(web_launch.WebLaunchError, match="focused"):
        web_launch.open_web_app("http://127.0.0.1:5005")


def test_concurrent_launches_wait_for_one_window_to_map(monkeypatch):
    mapped = threading.Event()
    launches = []
    results = []
    monkeypatch.setattr(web_launch, "_wait_for_web", lambda _url: None)
    monkeypatch.setattr(web_launch, "_has_hyprland", lambda: True)
    monkeypatch.setattr(web_launch, "_focus_existing", lambda _url: mapped.is_set())
    monkeypatch.setattr(web_launch.shutil, "which", lambda _name: "/usr/bin/brave")

    def launch(command, **_kwargs):
        launches.append(command)
        threading.Timer(0.1, mapped.set).start()
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(web_launch.subprocess, "Popen", launch)
    threads = [
        threading.Thread(
            target=lambda: results.append(web_launch.open_web_app("http://127.0.0.1:5005"))
        )
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    assert len(launches) == 1
    assert sorted(result["reused"] for result in results) == [False, True]


def test_unmapped_window_is_not_relaunched_immediately(monkeypatch):
    launches = []
    monkeypatch.setattr(web_launch, "_wait_for_web", lambda _url: None)
    monkeypatch.setattr(web_launch, "_focus_existing", lambda _url: False)
    monkeypatch.setattr(web_launch.shutil, "which", lambda _name: "/usr/bin/brave")
    monkeypatch.setattr(
        web_launch.subprocess, "Popen", lambda command, **_kwargs: launches.append(command)
    )
    web_launch.open_web_app("http://127.0.0.1:5005")
    with pytest.raises(web_launch.WebLaunchError, match="already requested"):
        web_launch.open_web_app("http://127.0.0.1:5005")
    assert len(launches) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1:5005/?token=private",
        "http://user:private@localhost:5005",
        "http://localhost:5005/#private",
    ],
)
def test_web_launcher_rejects_remote_or_credential_urls(url):
    with pytest.raises(web_launch.WebLaunchError):
        web_launch.open_web_app(url)


def test_browser_dispatch_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(web_launch, "_wait_for_web", lambda _url: None)
    monkeypatch.setattr(web_launch, "_focus_existing", lambda _url: False)
    monkeypatch.setattr(web_launch.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        web_launch.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "synthetic-browser")
        ),
    )
    with pytest.raises(web_launch.WebLaunchError, match="browser"):
        web_launch.open_web_app("http://127.0.0.1:5005")
