"""Package and manage Thursday's force-installed Brave extension on Linux."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from assistant.integrations.browser_bridge import HELPER_VERSION, helper_data_dir

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "assistant" / "browser_extension"
DEFAULT_POLICY = Path("/etc/brave/policies/managed/thursday-helper.json")
LEGACY_LAUNCHER_SHA256 = "08a28e281364547bfeb8d5908a2a7be05d1cd50742c1c23b3d98b2664dee73e2"


def _policy_path() -> Path:
    return Path(os.getenv("THURSDAY_BRAVE_POLICY_PATH", str(DEFAULT_POLICY)))


def _binary() -> str | None:
    return shutil.which("brave") or shutil.which("brave-browser")


def _privileged(command: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return command
    if sys.stdin.isatty() and shutil.which("sudo"):
        return ["sudo", *command]
    if shutil.which("pkexec"):
        return ["pkexec", *command]
    raise RuntimeError("sudo or pkexec is required to manage the Brave helper policy.")


def _extension_id(pem_path: Path) -> str:
    result = subprocess.run(
        ["openssl", "pkey", "-in", str(pem_path), "-pubout", "-outform", "DER"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "openssl failed")
    digest = hashlib.sha256(result.stdout).digest()[:16]
    return "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def status() -> dict[str, Any]:
    data_dir = helper_data_dir()
    metadata = _read_json(data_dir / "installation.json")
    policy = _read_json(_policy_path())
    entries = policy.get("ExtensionInstallForcelist", [])
    extension_id = str(metadata.get("extension_id") or "")
    policy_active = isinstance(entries, list) and any(
        str(item).split(";", 1)[0] == extension_id for item in entries
    )
    return {
        "installed": bool(extension_id and (data_dir / "thursday-helper.crx").is_file()),
        "policy_active": policy_active,
        "extension_id": extension_id or None,
        "version": metadata.get("version"),
        "current": metadata.get("version") == HELPER_VERSION and policy_active,
        "policy_path": str(_policy_path()),
    }


def _prepare_bundle(data_dir: Path) -> tuple[str, Path]:
    binary = _binary()
    if not binary:
        raise RuntimeError("Brave is not installed.")
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to package the Brave helper.")
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    token_path = data_dir / "token"
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        os.chmod(token_path, 0o600)
    token = token_path.read_text(encoding="utf-8").strip()
    key_path = data_dir / "thursday-helper.pem"

    with tempfile.TemporaryDirectory(prefix="thursday-helper-") as temporary:
        stage = Path(temporary) / "extension"
        shutil.copytree(SOURCE_DIR, stage)
        manifest_path = stage / "manifest.json"
        manifest = _read_json(manifest_path)
        manifest["version"] = HELPER_VERSION
        update_uri = (data_dir / "updates.xml").resolve().as_uri()
        manifest["update_url"] = update_uri
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (stage / "config.js").write_text(
            "globalThis.THURSDAY_HELPER_CONFIG = "
            + json.dumps({"token": token, "version": HELPER_VERSION})
            + ";\n",
            encoding="utf-8",
        )
        command = [binary, f"--pack-extension={stage}"]
        if key_path.exists():
            command.append(f"--pack-extension-key={key_path}")
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Brave could not package the helper.")
        generated_crx = stage.with_suffix(".crx")
        generated_pem = stage.with_suffix(".pem")
        if generated_pem.exists() and not key_path.exists():
            shutil.move(generated_pem, key_path)
            os.chmod(key_path, 0o600)
        if not generated_crx.exists() or not key_path.exists():
            raise RuntimeError("Brave did not produce the expected CRX and signing key.")
        shutil.move(generated_crx, data_dir / "thursday-helper.crx")

    extension_id = _extension_id(key_path)
    crx_uri = (data_dir / "thursday-helper.crx").resolve().as_uri()
    update_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gupdate xmlns="http://www.google.com/update2/response" protocol="2.0">\n'
        f'  <app appid="{extension_id}"><updatecheck codebase="{crx_uri}" '
        f'version="{HELPER_VERSION}" /></app>\n'
        "</gupdate>\n"
    )
    (data_dir / "updates.xml").write_text(update_xml, encoding="utf-8")
    (data_dir / "installation.json").write_text(
        json.dumps({"extension_id": extension_id, "version": HELPER_VERSION}, indent=2) + "\n",
        encoding="utf-8",
    )
    return extension_id, data_dir / "updates.xml"


def _run_privileged(command: list[str], failure: str) -> None:
    result = subprocess.run(
        _privileged(command), capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or failure)


def install(
    *,
    apply_policy: bool = True,
    rotate_token: bool = False,
    force_reinstall: bool = False,
) -> dict[str, Any]:
    data_dir = helper_data_dir()
    if rotate_token:
        token_path = data_dir / "token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        token_path.chmod(0o600)
    extension_id, update_manifest = _prepare_bundle(data_dir)
    policy = {"ExtensionInstallForcelist": [f"{extension_id};{update_manifest.resolve().as_uri()}"]}
    policy_path = _policy_path()
    if apply_policy:
        with tempfile.NamedTemporaryFile(
            "w", prefix="thursday-policy-", suffix=".json", delete=False
        ) as handle:
            json.dump(policy, handle, indent=2)
            handle.write("\n")
            staged_policy = Path(handle.name)
        try:
            if force_reinstall and policy_path.exists():
                _run_privileged(
                    ["rm", "-f", str(policy_path)],
                    "Brave helper policy removal failed.",
                )
                extension_root = (
                    Path.home()
                    / ".config/BraveSoftware/Brave-Browser/Default/Extensions"
                    / extension_id
                )
                deadline = time.monotonic() + 20
                while extension_root.exists() and time.monotonic() < deadline:
                    time.sleep(0.5)
            _run_privileged(
                ["install", "-Dm644", str(staged_policy), str(policy_path)],
                "Brave helper policy installation failed.",
            )
        finally:
            staged_policy.unlink(missing_ok=True)
        legacy_launcher = Path.home() / ".local/share/applications/brave-browser.desktop"
        try:
            if hashlib.sha256(legacy_launcher.read_bytes()).hexdigest() == LEGACY_LAUNCHER_SHA256:
                legacy_launcher.unlink()
        except OSError:
            pass
    return {"extension_id": extension_id, "version": HELPER_VERSION, "policy": policy}


def uninstall(*, remove_policy: bool = True) -> dict[str, Any]:
    policy_path = _policy_path()
    if remove_policy and policy_path.exists():
        _run_privileged(["rm", "-f", str(policy_path)], "Brave helper policy removal failed.")
    data_dir = helper_data_dir()
    if data_dir.is_dir():
        shutil.rmtree(data_dir)
    return {"removed": True, "policy_path": str(policy_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Thursday's Brave helper.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-policy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--rotate-token", action="store_true")
    parser.add_argument("--force-reinstall", action="store_true")
    args = parser.parse_args()
    if args.install:
        result = install(
            apply_policy=not args.no_policy,
            rotate_token=args.rotate_token,
            force_reinstall=args.force_reinstall,
        )
    elif args.uninstall:
        result = uninstall(remove_policy=not args.no_policy)
    else:
        result = status()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
