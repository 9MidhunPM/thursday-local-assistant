"""Explicit, optional installation of the private Kokoro CPU speech runtime."""

from __future__ import annotations

# Setup errors are fixed messages and never include subprocess output or credentials.
# ruff: noqa: TRY003
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ASSETS = (
    {
        "name": "kokoro-v1.0.fp16.onnx",
        "url": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.1/kokoro-v1.0.fp16.onnx",
        "sha256": "f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96",
        "size": 163527961,
    },
    {
        "name": "voices-v1.0.bin",
        "url": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
        "model-files-v1.1/voices-v1.0.bin",
        "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        "size": 28214398,
    },
)
_MARKER = ".thursday-neural-runtime.json"
_IDENTITY = {"application": "thursday", "runtime": "kokoro", "schema": 1}


class SetupError(RuntimeError):
    """A sanitized optional-runtime setup failure."""


def _path(value: str | Path) -> Path:
    # Resolving bin/python would discard the virtual environment's identity.
    return Path(value).expanduser().absolute()


def python_path() -> Path:
    override = os.environ.get("THURSDAY_NEURAL_PYTHON")
    if override:
        return _path(override)
    root = _path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    return root / "thursday/neural-venv/bin/python"


def models_dir() -> Path:
    override = os.environ.get("THURSDAY_NEURAL_MODELS")
    if override:
        return _path(override)
    root = _path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return root / "thursday/voices/kokoro"


def _safe_parents(path: Path) -> None:
    for parent in (*reversed(path.parents), path):
        if parent.is_symlink():
            raise SetupError("Neural storage cannot use symbolic-link directories.")
        if parent.exists() and not parent.is_dir():
            raise SetupError("Neural storage requires directories.")


def _private_directory(path: Path) -> Path:
    _safe_parents(path)
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700)
    info = path.lstat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
        raise SetupError("Neural storage must belong to the current user.")
    path.chmod(0o700)
    return path


def _regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
    except OSError:
        return False


def _verified(path: Path, asset: dict) -> bool:
    if not _regular_file(path) or path.stat().st_size != asset["size"]:
        return False
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest() == asset["sha256"]
    except OSError:
        return False


def verify_models(path: Path | None = None) -> bool:
    directory = _path(path) if path is not None else models_dir()
    try:
        _safe_parents(directory)
        return all(_verified(directory / asset["name"], asset) for asset in ASSETS)
    except (OSError, SetupError):
        return False


def _write_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".thursday-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _run(command: list[str], *, timeout: float = 600) -> str:
    # Ignore injected index/config options; only hash-verified public wheels install.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON"))
    }
    environment["UV_NO_CONFIG"] = "1"
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=False, timeout=timeout, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError("Neural runtime command failed or timed out.") from exc
    if result.returncode:
        raise SetupError("Neural runtime setup failed; check uv and network availability.")
    return result.stdout.strip()


def _install_runtime(selector: str) -> None:
    uv = shutil.which("uv")
    if not uv:
        raise SetupError("Install uv before running optional neural voice setup.")
    interpreter = python_path()
    if interpreter.name not in {"python", "python3"} or interpreter.parent.name != "bin":
        raise SetupError("The neural interpreter must be inside a private venv/bin directory.")
    directory = interpreter.parent.parent
    _safe_parents(directory)
    marker = directory / _MARKER
    if directory.exists() and any(directory.iterdir()):
        if not _regular_file(marker):
            raise SetupError("Refusing to modify an unmanaged neural runtime directory.")
        try:
            owned = json.loads(marker.read_text()) == _IDENTITY
        except (OSError, ValueError):
            owned = False
        if not owned:
            raise SetupError("The neural runtime ownership marker is invalid.")
    _private_directory(directory)
    _write_json(marker, _IDENTITY)
    if not interpreter.exists():
        _run([uv, "venv", "--allow-existing", "--python", selector, str(directory)])
    result = _run(
        [str(interpreter), "-I", "-c", "import json,sys;print(json.dumps("
         "{'version':list(sys.version_info[:2]),'prefix':sys.prefix}))"],
        timeout=10,
    )
    try:
        identity = json.loads(result)
    except ValueError as exc:
        raise SetupError("The neural interpreter identity could not be verified.") from exc
    if not isinstance(identity, dict) or identity.get("version") != [3, 13]:
        raise SetupError("Kokoro currently requires the separate Python 3.13 runtime.")
    if identity.get("prefix") != str(directory):
        raise SetupError("Refusing to install outside the private neural virtual environment.")
    requirements = Path(__file__).with_name("neural-requirements.txt")
    _run([
        uv, "pip", "install", "--python", str(interpreter), "--require-hashes",
        "--only-binary", ":all:", "--default-index", "https://pypi.org/simple",
        "--requirements", str(requirements),
    ])
    directory.chmod(0o700)


def _install_asset(directory: Path, asset: dict, source: Path | None) -> None:
    target = directory / asset["name"]
    if target.is_symlink() or (target.exists() and not _regular_file(target)):
        raise SetupError("Refusing to replace an unsafe model file.")
    if _verified(target, asset):
        target.chmod(0o600)
        return
    fd, name = tempfile.mkstemp(prefix=".download-", dir=directory)
    temporary = Path(name)
    try:
        if source is not None:
            original = source / asset["name"]
            if not _verified(original, asset):
                raise SetupError("Imported neural model failed SHA256 verification.")
            stream = original.open("rb")
        else:
            stream = urllib.request.urlopen(asset["url"], timeout=30)
        destination = os.fdopen(fd, "wb")
        fd = -1
        with destination, stream:
            total, deadline = 0, time.monotonic() + 600
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > asset["size"] or time.monotonic() > deadline:
                    raise SetupError("Neural model download exceeded its size or time limit.")
                destination.write(chunk)
        if not _verified(temporary, asset):
            raise SetupError("Downloaded neural model failed SHA256 verification.")
        temporary.replace(target)
    finally:
        if fd != -1:
            with contextlib.suppress(OSError):
                os.close(fd)
        temporary.unlink(missing_ok=True)


def install(*, python: str = "3.13", import_models: Path | None = None) -> dict:
    """Install only when explicitly called; normal speech never downloads assets."""
    source = _path(import_models) if import_models is not None else None
    if source is not None and not verify_models(source):
        raise SetupError("Imported neural models are missing or failed SHA256 verification.")
    _install_runtime(python)
    directory = _private_directory(models_dir())
    for asset in ASSETS:
        _install_asset(directory, asset, source)
    manifest = {
        "schema": 1, "model": "Kokoro-82M-v1.0", "voice": "bm_george",
        "assets": list(ASSETS), "verified": True, "model_license": "Apache-2.0",
        "model_card": "https://huggingface.co/hexgrad/Kokoro-82M/blob/"
        "f3ff3571791e39611d31c381e3a41a3af07b4987/README.md",
        "wrapper_license": "MIT", "phonemizer_engine_license": "GPL-3.0",
    }
    _write_json(directory / "manifest.json", manifest)
    return {"installed": True, "model_verified": verify_models(directory)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default="3.13", help="Compatible Python 3.13 interpreter")
    parser.add_argument("--import-models", type=Path, help="Copy already verified model assets")
    args = parser.parse_args(argv)
    old_umask = os.umask(0o077)
    try:
        result = install(python=args.python, import_models=args.import_models)
    except SetupError as exc:
        print(f"Neural setup failed: {exc}")
        return 1
    except (OSError, ValueError):
        print("Neural setup failed. Check uv, Python 3.13, network, and private storage.")
        return 1
    else:
        print(json.dumps(result))
        return 0
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    raise SystemExit(main())
