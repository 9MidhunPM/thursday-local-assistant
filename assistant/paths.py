"""Private, user-owned storage for transient desktop data."""

from __future__ import annotations

# Storage errors are deliberately fixed, credential-free user messages.
# ruff: noqa: TRY003
import os
import stat
import tempfile
import time
from pathlib import Path


def private_dir(path: Path) -> Path:
    """Create a private directory, refusing links and foreign ownership."""
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError("Thursday storage must be a user-owned directory.")
    path.chmod(0o700)
    return path


def runtime_dir(subdirectory: str | None = None) -> Path:
    """Return Thursday's private directory in the graphical session runtime."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    root = (Path(base) / "thursday" if base
            else Path(tempfile.gettempdir()) / f"thursday-{os.getuid()}")
    root = private_dir(root)
    if subdirectory is not None:
        if Path(subdirectory).name != subdirectory or subdirectory in {"", ".", ".."}:
            raise ValueError("Runtime subdirectory must be a single name.")
        root = private_dir(root / subdirectory)
    return root


def private_write(path: Path, data: bytes | str) -> None:
    """Atomically replace a file without exposing an intermediate public copy."""
    path = Path(path)
    private_dir(path.parent)
    content = data.encode("utf-8") if isinstance(data, str) else data
    fd, temporary = tempfile.mkstemp(prefix=".thursday-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def purge_runtime_files(directory: Path, *, older_than: float = 600) -> None:
    """Bound transient storage without following links or touching other users."""
    cutoff = time.time() - older_than
    for path in directory.iterdir():
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mtime < cutoff:
            path.unlink(missing_ok=True)
