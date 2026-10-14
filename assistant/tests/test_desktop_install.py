from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.desktop_install import DesktopInstaller, InstallError

_SHELL = """//@ pragma UseQApplication
import Quickshell
import QtQuick
Scope {
    id: shell
    property bool barVisible: false
    Variants {
        model: Quickshell.screens
        Bar { requestedVisible: shell.barVisible }
    }
}
"""
_KEYBINDS = """local mainMod = "SUPER"
hl.bind("SHIFT_R", hl.dsp.exec_cmd("qs ipc call bar toggle"), { non_consuming = true })
hl.bind("SHIFT_R",hl.dsp.exec_cmd("qs ipc call bar hide"),{ release = true, non_consuming = true })
hl.bind(mainMod .. " + C", hl.dsp.exec_cmd("~/.config/hypr/scripts/thursday-open.sh"))
hl.bind("ALT + SPACE", hl.dsp.exec_cmd("~/.config/hypr/scripts/thursday-focus.sh"))
hl.bind("SUPER + B", hl.dsp.exec_cmd("brave"))
"""


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, arguments, **_kwargs):
        self.calls.append(arguments)
        return SimpleNamespace(
            returncode=0,
            stdout="ActiveState=inactive\nUnitFileState=disabled\nNRestarts=0\nExecMainStatus=0\n",
            stderr="synthetic-private-service-detail",
        )


@pytest.fixture
def desktop(tmp_path):
    repo = tmp_path / "checkout with spaces"
    config, state, data = (tmp_path / name for name in ("config", "state", "data"))
    templates = Path(__file__).resolve().parents[2] / "desktop/templates"
    shutil.copytree(templates, repo / "desktop/templates")
    for relative, text in {
        ".venv/bin/python3": "fixture interpreter\n",
        "desktop/quickshell/Thursday.qml": "import QtQuick\nItem {}\n",
        ".env": "SYNTHETIC_PRIVATE_VALUE=keep-me\n",
        "assistant/database/memory.db": "synthetic-private-memory",
        "assistant/logs/thursday.log": "synthetic-private-log",
        "unrelated.txt": "keep permissions\n",
    }.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(0o644)
    originals = {
        "quickshell/shell.qml": _SHELL,
        "quickshell/Bar.qml": (
            "import QtQuick\nItem {\n    ThursdayVoice {}\n    OtherPanel {}\n}\n"
        ),
        "quickshell/ThursdayButton.qml": (
            'Item { property string launcher: "thursday-server.sh" }\n'
        ),
        "hypr/config/autostart.lua": (
            'hl.on("hyprland.start", function()\n    hl.exec_cmd("other-app")\nend)\n'
        ),
        "quickshell/config/Theme.qml": "pragma Singleton\nimport QtQuick\nQtObject {}\n",
        "hypr/config/keybinds.lua": _KEYBINDS,
        "hypr/scripts/thursday-open.sh": "old open\n",
        "hypr/scripts/thursday-focus.sh": "old focus\n",
        "hypr/scripts/thursday-server.sh": "old server\n",
        "hypr/scripts/thursday-toggle.sh": "unreferenced toggle must stay\n",
        "systemd/user/thursday-hotkeys.service": "old supervised hotkey unit\n",
    }
    for relative, text in originals.items():
        target = config / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(0o644)
    runner = Runner()
    installer = DesktopInstaller(
        repo_root=repo, config_home=config, state_home=state, data_home=data, runner=runner
    )
    return SimpleNamespace(
        repo=repo,
        config=config,
        state=state,
        data=data,
        runner=runner,
        installer=installer,
        originals=originals,
        base=tmp_path,
    )


def snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            content = ("link", str(path.readlink()))
        elif path.is_file():
            content = ("file", path.read_bytes())
        else:
            content = ("directory", None)
        result[str(path.relative_to(root))] = (*content, stat.S_IMODE(path.lstat().st_mode))
    return result


def test_dry_run_has_no_filesystem_or_service_mutations(desktop):
    before = snapshot(desktop.base)
    report = desktop.installer.install(dry_run=True)
    assert report["ok"]
    assert "config:quickshell/ThursdayNative" in report["files"]
    assert snapshot(desktop.base) == before
    assert not desktop.runner.calls


def test_install_is_surgical_private_and_repeatable(desktop):
    desktop.installer.install(no_start=True)
    shell = (desktop.config / "quickshell/shell.qml").read_text()
    assert shell.count("ThursdayNative.Thursday") == 1
    assert 'import "root:/config"' in shell
    assert "theme: Theme" in shell
    assert shell.index("ThursdayNative.Thursday") < shell.index("Variants {")
    assert "property bool barVisible: false" in shell
    assert "ThursdayVoice" not in (desktop.config / "quickshell/Bar.qml").read_text()
    assert "OtherPanel {}" in (desktop.config / "quickshell/Bar.qml").read_text()
    bindings = (desktop.config / "hypr/config/keybinds.lua").read_text()
    for line in _KEYBINDS.splitlines():
        if "SHIFT_R" in line or "brave" in line:
            assert line in bindings
    assert '"qs ipc call thursday toggle"' in bindings
    assert '"qs ipc call thursday focus"' in bindings
    startup = (desktop.config / "hypr/config/autostart.lua").read_text()
    assert desktop.originals["hypr/config/autostart.lua"] in startup
    assert startup.count("BEGIN THURSDAY SESSION") == 1
    assert (
        desktop.config / "quickshell/ThursdayNative"
    ).resolve() == desktop.repo / "desktop/quickshell"
    assert (
        desktop.config / "hypr/scripts/thursday-toggle.sh"
    ).read_text() == "unreferenced toggle must stay\n"
    assert stat.S_IMODE(desktop.installer.storage.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600 for path in desktop.installer.storage.iterdir()
    )
    assert stat.S_IMODE((desktop.repo / ".env").stat().st_mode) == 0o600
    assert stat.S_IMODE((desktop.repo / "assistant/database").stat().st_mode) == 0o700
    assert stat.S_IMODE((desktop.repo / "assistant/logs/thursday.log").stat().st_mode) == 0o600
    assert stat.S_IMODE((desktop.repo / "unrelated.txt").stat().st_mode) == 0o644
    before = snapshot(desktop.base)
    desktop.installer.install(no_start=True)
    assert snapshot(desktop.base) == before
    assert not any("restart" in command for command in desktop.runner.calls)


def test_units_and_default_start_use_supervision_and_only_allowlisted_environment(desktop):
    desktop.installer.install()
    backend = (desktop.config / "systemd/user/thursday.service").read_text()
    hotkeys = (desktop.config / "systemd/user/thursday-hotkeys.service").read_text()
    for unit in (backend, hotkeys):
        assert "UMask=0077" in unit
        assert "Restart=on-failure" in unit
        assert "RestartPreventExitStatus=78" in unit
        assert "StartLimitBurst=3" in unit
        assert "WantedBy=graphical-session.target" in unit
        assert f'WorkingDirectory="{desktop.repo}"' in unit
    assert "-m assistant.main --web --no-browser" in backend
    assert "Requires=thursday.service" in hotkeys
    assert "-m assistant.hotkeys" in hotkeys
    startup = (desktop.config / "thursday/session-start.sh").read_text()
    assert "HYPRLAND_INSTANCE_SIGNATURE" in startup
    assert 'import-environment "${session_vars[@]}"' in startup
    assert "OPENAI_API_KEY" not in startup
    assert "THURSDAY_API_TOKEN" not in startup
    assert desktop.runner.calls[-1] == [str(desktop.config / "thursday/session-start.sh")]
    button = (desktop.config / "quickshell/ThursdayButton.qml").read_text()
    assert '"thursday", "toggle"' in button
    assert "XMLHttpRequest" not in button
    assert "5005" not in button


def test_uninstall_restores_originals_and_removes_only_generated_files(desktop):
    desktop.installer.install(no_start=True)
    report = desktop.installer.uninstall(no_start=True)
    assert report["ok"]
    for relative, original in desktop.originals.items():
        assert (desktop.config / relative).read_text() == original
        assert stat.S_IMODE((desktop.config / relative).stat().st_mode) == 0o644
    assert not (desktop.config / "quickshell/ThursdayNative").is_symlink()
    assert not (desktop.data / "applications/thursday.desktop").exists()
    assert not (desktop.config / "systemd/user/thursday.service").exists()
    assert (desktop.repo / "desktop/quickshell/Thursday.qml").exists()
    assert not desktop.installer._load_manifest()["files"]


def test_uninstall_preserves_user_edits(desktop):
    desktop.installer.install(no_start=True)
    shell = desktop.config / "quickshell/shell.qml"
    edited = shell.read_text() + "// User's new setting\n"
    shell.write_text(edited)
    before = snapshot(desktop.base)
    desktop.runner.calls.clear()
    report = desktop.installer.uninstall(no_start=True)
    assert report["preserved"] == ["config:quickshell/shell.qml"]
    assert shell.read_text() == edited
    assert "config:quickshell/shell.qml" in desktop.installer._load_manifest()["files"]
    assert report["blocked"]
    assert snapshot(desktop.base) == before
    assert not desktop.runner.calls
    assert (desktop.config / "quickshell/ThursdayNative").is_symlink()


def test_reinstall_refuses_modified_managed_file_before_mutating(desktop):
    desktop.installer.install(no_start=True)
    path = desktop.config / "quickshell/ThursdayButton.qml"
    path.write_text(path.read_text() + "// User edit\n")
    before = snapshot(desktop.base)
    with pytest.raises(InstallError, match="managed_file_changed"):
        desktop.installer.install(no_start=True)
    assert snapshot(desktop.base) == before


@pytest.mark.parametrize("target", ["systemd/user/thursday.service", "quickshell/ThursdayNative"])
def test_existing_symlink_targets_are_refused_without_writes(desktop, target):
    foreign = desktop.base / "outside.txt"
    foreign.write_text("outside content\n")
    path = desktop.config / target
    path.symlink_to(foreign)
    before = snapshot(desktop.base)
    with pytest.raises(InstallError, match="symlink_target"):
        desktop.installer.install(no_start=True)
    assert snapshot(desktop.base) == before


def test_symlink_parent_is_refused(desktop):
    outside = desktop.base / "elsewhere"
    outside.mkdir()
    shutil.rmtree(desktop.config / "systemd")
    (desktop.config / "systemd").symlink_to(outside, target_is_directory=True)
    before = snapshot(desktop.base)
    with pytest.raises(InstallError, match="symlink_target"):
        desktop.installer.install(no_start=True)
    assert snapshot(desktop.base) == before


def test_symlink_private_storage_is_refused_without_touching_target(desktop):
    private = desktop.repo / ".env"
    private.unlink()
    outside = desktop.base / "outside.env"
    outside.write_text("synthetic-private-credential\n")
    private.symlink_to(outside)
    before = snapshot(desktop.base)
    with pytest.raises(InstallError, match="symlink_target"):
        desktop.installer.install(no_start=True)
    assert snapshot(desktop.base) == before


def test_foreign_owned_config_is_refused(desktop, monkeypatch):
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    with pytest.raises(InstallError, match="foreign_owned_target"):
        desktop.installer.install(no_start=True)
    assert not desktop.installer.storage.exists()


def test_changed_backup_is_rejected_before_restoration_or_service_changes(desktop):
    desktop.installer.install(no_start=True)
    backup = next(desktop.installer.storage.glob("*.bak"))
    backup.write_text("modified backup\n")
    before = snapshot(desktop.base)
    desktop.runner.calls.clear()
    with pytest.raises(InstallError, match="backup_changed"):
        desktop.installer.uninstall(no_start=True)
    assert snapshot(desktop.base) == before
    assert not desktop.runner.calls


def test_interrupted_install_can_resume_without_losing_originals(desktop, monkeypatch):
    real_write = desktop.installer._write
    failure_path = desktop.config / "quickshell/Bar.qml"

    def interrupt(path, content, mode):
        if path == failure_path:
            raise OSError("simulated interrupted write")  # noqa: TRY003 - synthetic I/O failure
        return real_write(path, content, mode)

    monkeypatch.setattr(desktop.installer, "_write", interrupt)
    with pytest.raises(OSError, match="simulated interrupted"):
        desktop.installer.install(no_start=True)
    monkeypatch.setattr(desktop.installer, "_write", real_write)
    desktop.installer.install(no_start=True)
    desktop.installer.uninstall(no_start=True)
    for relative, original in desktop.originals.items():
        assert (desktop.config / relative).read_text() == original


def test_private_contents_are_not_read_and_doctor_is_sanitized(desktop, monkeypatch):
    real_read = Path.read_bytes
    private_paths = {
        desktop.repo / relative
        for relative in (
            ".env",
            "assistant/database/memory.db",
            "assistant/logs/thursday.log",
        )
    }

    def guarded_read(path):
        if path in private_paths:
            pytest.fail("The installer tried to read private runtime contents.")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "synthetic-private-session-value")
    desktop.installer.install(no_start=True)
    report = desktop.installer.doctor()
    assert report["installed"]
    assert report["session"]["DBUS_SESSION_BUS_ADDRESS"] is True
    assert "synthetic-private" not in json.dumps(report)
    assert "SYNTHETIC_PRIVATE" not in json.dumps(report)
    assert all(isinstance(value, bool) for value in report["dependencies"].values())


def test_manifest_cannot_redirect_restore_to_unrelated_paths(desktop):
    desktop.installer.install(no_start=True)
    manifest = desktop.installer._load_manifest()
    entry = next(iter(manifest["files"].values()))
    manifest["files"] = {"config:../outside": entry}
    desktop.installer.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(InstallError, match="invalid_manifest"):
        desktop.installer.uninstall(no_start=True)
