from __future__ import annotations

import os

import pytest

from assistant.paths import private_write, purge_runtime_files, runtime_dir


def test_runtime_and_atomic_files_are_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    root = runtime_dir("audio")
    path = root / "response.mp3"
    private_write(path, b"fixture audio")
    assert root.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == b"fixture audio"
    private_write(path, b"replacement")
    assert path.read_bytes() == b"replacement"
    assert not list(root.glob(".thursday-*"))


def test_runtime_refuses_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "thursday").symlink_to(other, target_is_directory=True)
    with pytest.raises(PermissionError):
        runtime_dir()


def test_runtime_rejects_traversal_and_purges_only_old_regular_files(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="single name"):
        runtime_dir("../other")
    root = runtime_dir()
    old, current = root / "old.wav", root / "new.wav"
    private_write(old, b"old")
    private_write(current, b"new")
    os.utime(old, (1, 1))
    link = root / "link"
    link.symlink_to(current)
    purge_runtime_files(root)
    assert not old.exists()
    assert current.exists()
    assert link.is_symlink()
