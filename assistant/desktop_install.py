"""Surgical, reversible installation into an existing Hyprland/Quickshell session."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SERVICES = ("thursday.service", "thursday-hotkeys.service")
SESSION_VARIABLES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "XDG_CURRENT_DESKTOP",
)
_ALLOWED = {
    "config": {
        "systemd/user/thursday.service",
        "systemd/user/thursday-hotkeys.service",
        "thursday/session-start.sh",
        "thursday/open.sh",
        "quickshell/ThursdayNative",
        "quickshell/shell.qml",
        "quickshell/Bar.qml",
        "quickshell/ThursdayButton.qml",
        "hypr/config/autostart.lua",
        "hypr/config/keybinds.lua",
        "hypr/scripts/thursday-open.sh",
        "hypr/scripts/thursday-focus.sh",
        "hypr/scripts/thursday-server.sh",
        "hypr/scripts/thursday-toggle.sh",
    },
    "data": {"applications/thursday.desktop"},
}


class InstallError(RuntimeError):
    """Stable diagnostics never include private file contents or command output."""


@dataclass(frozen=True)
class Change:
    root: str
    relative: str
    kind: str
    payload: str
    mode: int = 0o600

    @property
    def key(self):
        return f"{self.root}:{self.relative}"

    @property
    def digest(self):
        return _digest(self.kind, self.payload.encode())


def _digest(kind, content):
    return hashlib.sha256(kind.encode() + b"\0" + content).hexdigest()


def _quoted_systemd(path):
    value = str(path)
    if any(character in value for character in "\n\r\0"):
        raise InstallError("unsafe_path")
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        + '"'
    )


def _systemd_directory(path):
    """WorkingDirectory is a literal path, not an ExecStart argument."""
    value = str(path)
    if not Path(value).is_absolute() or any(character in value for character in "\n\r\0"):
        raise InstallError("unsafe_path")
    return value.replace("%", "%%")


def _without_block(text, label, comment):
    begin, end = f"{comment} BEGIN THURSDAY {label}", f"{comment} END THURSDAY {label}"
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise InstallError("ambiguous_managed_block")
    pattern = rf"(?ms)^[ \t]*{re.escape(begin)}\n.*?^[ \t]*{re.escape(end)}\n?"
    return re.sub(pattern, "", text)


def _shell(text, repo, *, theme=False):
    text = _without_block(_without_block(text, "IMPORT", "//"), "COMPANION", "//")
    imports = list(re.finditer(r"(?m)^import [^\n]+\n", text))
    roots = list(re.finditer(r"(?m)^(?:Scope|ShellRoot)\s*\{[ \t]*\n", text))
    if not imports or len(roots) != 1:
        raise InstallError("unsupported_quickshell_root")
    root = roots[0]
    theme_property = "; theme: Theme" if theme else ""
    component = (
        "    // BEGIN THURSDAY COMPANION\n"
        f"    ThursdayNative.Thursday {{ repoRoot: {json.dumps(str(repo))}{theme_property} }}\n"
        "    // END THURSDAY COMPANION\n"
    )
    text = text[: root.end()] + component + text[root.end() :]
    at = imports[-1].end()
    theme_import = (
        'import "root:/config"\n'
        if theme and not re.search(r'(?m)^import "root:/config"\s*$', text)
        else ""
    )
    return (
        text[:at]
        + (
            '// BEGIN THURSDAY IMPORT\nimport "ThursdayNative" as ThursdayNative\n'
            + theme_import
            + "// END THURSDAY IMPORT\n"
        )
        + text[at:]
    )


def _keybinds(text):
    selectors = (
        (r'(?:mainMod\s*\.\.\s*"\s*\+\s*C"|"SUPER\s*\+\s*C")', "toggle"),
        (r'"ALT\s*\+\s*SPACE"', "toggle"),
    )
    for selector, action in selectors:
        pattern = (
            rf"(?m)^(\s*hl\.bind\(\s*{selector}\s*,\s*hl\.dsp\.exec_cmd\()"
            r'"(?:[^"\\\n]|\\.)*"(\)\s*\)[^\n]*)$'
        )
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise InstallError("unsupported_thursday_keybinds")
        if "mainMod" in matches[0].group(1) and not re.search(
            r'(?m)^\s*local\s+mainMod\s*=\s*"SUPER"', text
        ):
            raise InstallError("unsupported_thursday_modifier")
        text = re.sub(
            pattern,
            lambda match, action=action: (
                match.group(1) + json.dumps(f"qs ipc call thursday {action}") + match.group(2)
            ),
            text,
        )
    return text


class DesktopInstaller:
    def __init__(
        self,
        *,
        repo_root=None,
        config_home=None,
        state_home=None,
        data_home=None,
        runner=subprocess.run,
    ):
        self.repo = Path(repo_root or Path(__file__).resolve().parents[1]).absolute()
        self.roots = {
            "config": Path(
                config_home or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
            ).absolute(),
            "data": Path(
                data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")
            ).absolute(),
        }
        state = Path(state_home or os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        self.state_home = state.absolute()
        self.storage = self.state_home / "thursday/desktop-install"
        self.manifest_path = self.storage / "manifest.json"
        self.runner = runner

    @staticmethod
    def _safe_path(path, root):
        """Reject symlink ancestors, special files, and foreign-owned targets."""
        if not path.is_relative_to(root):
            raise InstallError("unsafe_target")
        for ancestor in reversed((path, *path.parents)):
            if not ancestor.exists() and not ancestor.is_symlink():
                continue
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise InstallError("symlink_target")
            if ancestor.is_relative_to(root) and info.st_uid != os.getuid():
                raise InstallError("foreign_owned_target")
            if ancestor != path and not stat.S_ISDIR(info.st_mode):
                raise InstallError("unsafe_parent")

    def _target(self, root, relative, *, allow_link=False):
        if relative not in _ALLOWED.get(root, set()):
            raise InstallError("unsafe_manifest_target")
        path = self.roots[root] / relative
        self._safe_path(path.parent if allow_link else path, self.roots[root])
        if path.is_symlink() and allow_link and path.lstat().st_uid != os.getuid():
            raise InstallError("foreign_owned_target")
        if path.exists() and not path.is_symlink() and not path.is_file():
            raise InstallError("unexpected_target_type")
        return path

    def _read(self, relative):
        path = self._target("config", relative)
        if not path.is_file():
            raise InstallError("missing_existing_desktop_config")
        return path.read_text()

    def _template(self, name):
        return (self.repo / "desktop/templates" / name).read_text()

    def plan(self):
        module = self.repo / "desktop/quickshell"
        if not (module / "Thursday.qml").is_file():
            raise InstallError("missing_native_module")
        if not (self.repo / ".venv/bin/python3").is_file():
            raise InstallError("missing_virtual_environment")
        shell = self._read("quickshell/shell.qml")
        bar = self._read("quickshell/Bar.qml")
        autostart = self._read("hypr/config/autostart.lua")
        keybinds = self._read("hypr/config/keybinds.lua")
        old_button = self._read("quickshell/ThursdayButton.qml")
        previous_files = self._load_manifest()["files"]
        theme = (self.roots["config"] / "quickshell/config/Theme.qml").is_file()
        references = "\n".join((shell, bar, autostart, keybinds, old_button))
        bar = re.sub(r"(?m)^[ \t]*ThursdayVoice\s*\{\s*\}[ \t]*\n?", "", bar)
        if re.search(r"\bThursdayVoice\s*\{", bar):
            raise InstallError("custom_voice_component_requires_review")
        wrapper = self.roots["config"] / "thursday/session-start.sh"
        autostart = _without_block(autostart, "SESSION", "--")
        if autostart and not autostart.endswith("\n"):
            autostart += "\n"
        autostart += (
            "-- BEGIN THURSDAY SESSION\n"
            'hl.on("hyprland.start", function()\n'
            f"    hl.exec_cmd({json.dumps(shlex.quote(str(wrapper)))})\n"
            "end)\n"
            'hl.on("hyprland.shutdown", function()\n'
            '    os.execute("timeout 25s systemctl --user stop '
            'thursday-hotkeys.service thursday.service")\n'
            "end)\n-- END THURSDAY SESSION\n"
        )
        substitutions = {
            "@DIRECTORY_SYSTEMD@": _systemd_directory(self.repo),
            "@PYTHON_SYSTEMD@": _quoted_systemd(self.repo / ".venv/bin/python3"),
        }
        changes = []
        for service in SERVICES:
            content = self._template(service)
            for token, value in substitutions.items():
                content = content.replace(token, value)
            changes.append(Change("config", "systemd/user/" + service, "file", content))
        changes.extend(
            (
                Change(
                    "config",
                    "thursday/session-start.sh",
                    "file",
                    self._template("session-start.sh"),
                    0o700,
                ),
                Change("config", "thursday/open.sh", "file", self._launcher("focus"), 0o700),
                Change("config", "quickshell/ThursdayNative", "symlink", str(module)),
                Change(
                    "config", "quickshell/shell.qml", "file", _shell(shell, self.repo, theme=theme)
                ),
                Change("config", "quickshell/Bar.qml", "file", bar),
                Change(
                    "config",
                    "quickshell/ThursdayButton.qml",
                    "file",
                    self._template("ThursdayButton.qml"),
                ),
                Change("config", "hypr/config/autostart.lua", "file", autostart),
                Change("config", "hypr/config/keybinds.lua", "file", _keybinds(keybinds)),
            )
        )
        for name, action in (
            ("open", "toggle"),
            ("focus", "focus"),
            ("server", None),
            ("toggle", "toggle"),
        ):
            relative = f"hypr/scripts/thursday-{name}.sh"
            if f"thursday-{name}.sh" in references or "config:" + relative in previous_files:
                changes.append(Change("config", relative, "file", self._launcher(action), 0o700))
        changes.append(
            Change(
                "data",
                "applications/thursday.desktop",
                "file",
                self._template("thursday.desktop"),
            )
        )
        return changes

    @staticmethod
    def _launcher(action):
        content = (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "timeout 40s systemctl --user start thursday.service\n"
        )
        if action:
            content += f"exec timeout 5s qs ipc call thursday {action}\n"
        return content

    def _load_manifest(self):
        self._safe_path(self.manifest_path, self.state_home)
        if not self.manifest_path.exists():
            return {"version": 1, "files": {}, "services_before": {}}
        if not self.manifest_path.is_file() or self.manifest_path.stat().st_size > 1_000_000:
            raise InstallError("invalid_manifest")
        try:
            manifest = json.loads(self.manifest_path.read_text())
            self._validate_manifest(manifest)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise InstallError("invalid_manifest") from exc
        return manifest

    @staticmethod
    def _validate_manifest(manifest):
        if (
            manifest["version"] != 1
            or not isinstance(manifest["files"], dict)
            or not isinstance(manifest["services_before"], dict)
        ):
            raise ValueError
        for key, entry in manifest["files"].items():
            root, relative = key.split(":", 1)
            if relative not in _ALLOWED.get(root, set()):
                raise ValueError
            if (
                entry["kind"] not in {"file", "symlink"}
                or not isinstance(entry["installed_hash"], str)
                or not isinstance(entry["applied"], bool)
            ):
                raise ValueError
            for field in ("installed_hash", "original_hash", "previous_hash"):
                value = entry.get(field)
                if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                    raise ValueError
            original = entry["original"]
            if original is not None and not re.fullmatch(r"[0-9a-f]{64}\.bak", original["backup"]):
                raise ValueError
            if original is not None and (
                not isinstance(original["mode"], int) or not 0 <= original["mode"] <= 0o777
            ):
                raise ValueError

    @staticmethod
    def _fingerprint(path):
        if path.is_symlink():
            return _digest("symlink", str(path.readlink()).encode())
        if not path.exists():
            return None
        if not path.is_file():
            raise InstallError("unexpected_target_type")
        return _digest("file", path.read_bytes())

    @staticmethod
    def _accepted_hashes(entry):
        hashes = {entry["installed_hash"]}
        if not entry.get("applied", True):
            hashes.update((entry.get("previous_hash"), entry.get("original_hash")))
        return hashes

    def _private_modes(self):
        paths = []
        env = self.repo / ".env"
        if env.exists() or env.is_symlink():
            self._safe_path(env, self.repo)
            if not env.is_file():
                raise InstallError("unsafe_private_storage")
            paths.append((env, 0o600))
        for relative in ("assistant/database", "assistant/logs"):
            root = self.repo / relative
            self._safe_path(root, self.repo)
            if not root.exists():
                continue
            if not root.is_dir():
                raise InstallError("unsafe_private_storage")
            for path in (root, *root.rglob("*")):
                self._safe_path(path, self.repo)
                if not path.is_file() and not path.is_dir():
                    raise InstallError("unsafe_private_storage")
                paths.append((path, 0o700 if path.is_dir() else 0o600))
        return paths

    @staticmethod
    def _write(path, content, mode):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".thursday-install-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                os.fchmod(stream.fileno(), mode)
            Path(temporary).replace(path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _save_manifest(self, manifest):
        self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.storage.chmod(0o700)
        self._write(self.manifest_path, json.dumps(manifest, indent=2).encode() + b"\n", 0o600)

    def _command(self, arguments):
        try:
            result = self.runner(arguments, capture_output=True, text=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError("service_command_unavailable") from exc
        if result.returncode:
            raise InstallError("service_command_failed")

    def _service_status(self):
        status = {}
        for service in SERVICES:
            try:
                result = self.runner(
                    [
                        "systemctl",
                        "--user",
                        "show",
                        service,
                        "--no-pager",
                        "-p",
                        "ActiveState",
                        "-p",
                        "SubState",
                        "-p",
                        "UnitFileState",
                        "-p",
                        "ExecMainStatus",
                        "-p",
                        "NRestarts",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                raw = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
                status[service] = {
                    "available": result.returncode == 0,
                    "active": raw.get("ActiveState") == "active",
                    "enabled": raw.get("UnitFileState") == "enabled",
                    "failed": raw.get("ActiveState") == "failed",
                    "restarts": int(raw.get("NRestarts", "0")),
                    "exit_status": int(raw.get("ExecMainStatus", "0")),
                }
            except (OSError, subprocess.TimeoutExpired, ValueError):
                status[service] = {"available": False, "active": False, "enabled": False}
        return status

    def install(self, *, dry_run=False, no_start=False):
        changes, manifest = self.plan(), self._load_manifest()
        permissions = self._private_modes()
        prepared = {}
        for change in changes:
            entry = manifest["files"].get(change.key)
            path = self._target(change.root, change.relative, allow_link=bool(entry))
            current_hash = self._fingerprint(path)
            if entry and current_hash not in self._accepted_hashes(entry):
                raise InstallError("managed_file_changed: " + change.key)
            if not entry:
                original = None
                if path.exists():
                    original = {
                        "backup": hashlib.sha256(change.key.encode()).hexdigest() + ".bak",
                        "mode": stat.S_IMODE(path.stat().st_mode),
                    }
                    if original["mode"] > 0o777:
                        raise InstallError("unsafe_file_mode")
                entry = {"original": original, "original_hash": current_hash}
            prepared[change.key] = {
                **entry,
                "installed_hash": change.digest,
                "previous_hash": current_hash,
                "kind": change.kind,
                "applied": False,
            }
            if current_hash == change.digest and entry.get("applied"):
                prepared[change.key] = entry
        report = {
            "ok": True,
            "action": "install",
            "dry_run": dry_run,
            "files": [change.key for change in changes],
            "private_paths": len(permissions),
        }
        if dry_run:
            return report
        self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.storage.chmod(0o700)
        for change in changes:
            if change.key not in manifest["files"] and prepared[change.key]["original"]:
                path = self._target(change.root, change.relative)
                backup = self.storage / prepared[change.key]["original"]["backup"]
                self._safe_path(backup, self.storage)
                self._write(backup, path.read_bytes(), 0o600)
        if not manifest["services_before"]:
            manifest["services_before"] = self._service_status()
        manifest["files"].update(prepared)
        self._save_manifest(manifest)
        for change in changes:
            path = self._target(change.root, change.relative, allow_link=True)
            current_hash = self._fingerprint(path)
            if current_hash not in self._accepted_hashes(manifest["files"][change.key]):
                raise InstallError("managed_file_changed: " + change.key)
            if current_hash != change.digest:
                if change.kind == "symlink":
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if path.exists() or path.is_symlink():
                        path.unlink()
                    path.symlink_to(change.payload, target_is_directory=True)
                else:
                    self._write(path, change.payload.encode(), change.mode)
            manifest["files"][change.key]["applied"] = True
            self._save_manifest(manifest)
        for path, mode in permissions:
            # fchmod does not follow a link swapped in after the preflight.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if info.st_uid != os.getuid():
                    raise InstallError("foreign_owned_target")
                if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
                    raise InstallError("unsafe_private_storage")
                os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
        self._command(["systemctl", "--user", "daemon-reload"])
        self._command(["systemctl", "--user", "enable", *SERVICES])
        if not no_start:
            self._command([str(self.roots["config"] / "thursday/session-start.sh")])
        return report

    def uninstall(self, *, dry_run=False, no_start=False):
        manifest = self._load_manifest()
        restorable, preserved, originals = [], [], {}
        for key, entry in manifest["files"].items():
            root, relative = key.split(":", 1)
            path = self._target(root, relative, allow_link=True)
            if self._fingerprint(path) in self._accepted_hashes(entry):
                restorable.append((key, path, entry))
                if entry["original"]:
                    backup = self.storage / entry["original"]["backup"]
                    self._safe_path(backup, self.state_home)
                    originals[key] = backup.read_bytes()
                    if _digest("file", originals[key]) != entry["original_hash"]:
                        raise InstallError("backup_changed")
            else:
                preserved.append(key)
        report = {
            "ok": not preserved,
            "action": "uninstall",
            "dry_run": dry_run,
            "restore": [] if preserved else [key for key, _, _ in restorable],
            "preserved": preserved,
            "blocked": bool(preserved),
        }
        if dry_run or preserved or not manifest["files"]:
            return report
        units = {"config:systemd/user/" + service for service in SERVICES}
        safe_services = not units.intersection(preserved)
        if safe_services:
            self._command(["systemctl", "--user", "stop", *reversed(SERVICES)])
            self._command(["systemctl", "--user", "disable", *SERVICES])
        for key, path, entry in reversed(restorable):
            root, relative = key.split(":", 1)
            path = self._target(root, relative, allow_link=True)
            if self._fingerprint(path) not in self._accepted_hashes(entry):
                raise InstallError("managed_file_changed: " + key)
            original = entry["original"]
            if original:
                self._write(path, originals[key], original["mode"])
            else:
                path.unlink(missing_ok=True)
            del manifest["files"][key]
            self._save_manifest(manifest)
        self._command(["systemctl", "--user", "daemon-reload"])
        if safe_services:
            for service, before in manifest["services_before"].items():
                if service not in SERVICES:
                    continue
                if before.get("enabled"):
                    self._command(["systemctl", "--user", "enable", service])
                if before.get("active") and not no_start:
                    self._command(["systemctl", "--user", "start", service])
        return report

    def doctor(self):
        dependencies = {
            name: importlib.util.find_spec(name) is not None
            for name in (
                "mcp",
                "hypruse",
                "evdev",
                "pyaudio",
                "speech_recognition",
                "edge_tts",
            )
        }
        services = self._service_status()
        problems = ["missing_" + name for name, present in dependencies.items() if not present]
        session = {name: bool(os.environ.get(name)) for name in SESSION_VARIABLES}
        if not all(session[name] for name in SESSION_VARIABLES if name != "DISPLAY"):
            problems.append("graphical_session_environment_incomplete")
        for service, status in services.items():
            if not status["available"]:
                problems.append(service + ":status_unavailable")
            elif not status["active"] or not status["enabled"]:
                problems.append(service + ":not_running_or_enabled")
        try:
            manifest = self._load_manifest()
            installed = bool(manifest["files"])
            if not installed:
                problems.append("desktop_not_installed")
            for key, entry in manifest["files"].items():
                root, relative = key.split(":", 1)
                path = self._target(root, relative, allow_link=True)
                if self._fingerprint(path) not in self._accepted_hashes(entry):
                    problems.append("managed_file_changed:" + key)
            if any(
                stat.S_IMODE(path.stat().st_mode) != mode for path, mode in self._private_modes()
            ):
                problems.append("private_storage_permissions")
        except (InstallError, OSError):
            installed = False
            problems.append("installation_needs_review")
        return {
            "ok": not problems,
            "dependencies": dependencies,
            "session": session,
            "commands": {name: bool(shutil.which(name)) for name in ("qs", "hyprctl", "systemctl")},
            "services": services,
            "installed": installed,
            "problems": problems,
        }


def main():
    parser = argparse.ArgumentParser(
        description="Install Thursday into the user's desktop session."
    )
    parser.add_argument("action", choices=("install", "doctor", "uninstall"))
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without writing or running services."
    )
    parser.add_argument("--no-start", action="store_true", help="Do not start or restart services.")
    args = parser.parse_args()
    try:
        installer = DesktopInstaller()
        report = (
            installer.doctor()
            if args.action == "doctor"
            else getattr(installer, args.action)(
                dry_run=args.dry_run,
                no_start=args.no_start,
            )
        )
    except (InstallError, OSError, UnicodeError) as exc:
        report = {
            "ok": False,
            "error": str(exc) if isinstance(exc, InstallError) else "installation_io_error",
        }
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
